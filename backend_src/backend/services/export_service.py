"""JSON, CSV and Excel exports for structured invoice data."""
from __future__ import annotations

import io
import json
from typing import Any, Dict, List

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from backend.schemas.invoice_schema import InvoiceDetail

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="D0D5DD")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
NUM_FMT = "#,##0.00"


def _safe(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def invoice_to_dict(inv: InvoiceDetail) -> Dict[str, Any]:
    return {
        "id": inv.id,
        "source_file": inv.original_filename,
        "status": inv.status,
        "needs_review": inv.needs_review,
        "processing_times": inv.processing_times,
        **inv.data.model_dump(),
        "validation": [i.model_dump() for i in inv.validation.issues],
    }


def export_json(inv: InvoiceDetail) -> bytes:
    return json.dumps(invoice_to_dict(inv), indent=2, ensure_ascii=False).encode("utf-8")


CSV_COLUMNS = [
    "invoice_id", "source_file", "invoice_number", "invoice_date", "due_date", "purchase_order_number", "reference_number",
    "currency", "invoice_type", "payment_terms", "supplier_name", "supplier_address", "supplier_email", "supplier_phone",
    "supplier_tax_id", "supplier_registration_number", "customer_name", "customer_address", "customer_email", "customer_phone",
    "customer_tax_id", "customer_registration_number", "subtotal", "taxable_amount", "discount", "cgst", "sgst", "igst",
    "other_charges", "shipping", "tax", "round_off", "grand_total", "amount_paid", "balance_due", "status",
    "item_no", "item_code", "item_description", "item_hsn_sac", "item_quantity", "item_unit", "item_unit_price",
    "item_discount", "item_tax_rate", "item_tax", "item_total",
]


def _flat_rows(inv: InvoiceDetail) -> List[Dict[str, Any]]:
    d = inv.data
    base = {
        "invoice_id": inv.id, "source_file": inv.original_filename, "invoice_number": d.invoice_number,
        "invoice_date": d.invoice_date, "due_date": d.due_date, "purchase_order_number": d.purchase_order_number,
        "reference_number": d.reference_number, "currency": d.currency, "invoice_type": d.invoice_type,
        "payment_terms": d.payment_terms,
        **{f"supplier_{k}": v for k, v in d.supplier.model_dump().items()},
        **{f"customer_{k}": v for k, v in d.customer.model_dump().items()},
        **d.financials.model_dump(), "status": inv.status,
    }
    if not d.items:
        return [base]
    return [
        {**base, "item_no": n, **{f"item_{k}": v for k, v in item.model_dump().items()}}
        for n, item in enumerate(d.items, start=1)
    ]


def export_csv(invoices: List[InvoiceDetail]) -> bytes:
    rows = [r for inv in invoices for r in _flat_rows(inv)]
    df = pd.DataFrame(rows, columns=CSV_COLUMNS)
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(_safe)
    return df.to_csv(index=False).encode("utf-8-sig")


def _write_table(ws, headers: List[str], rows: List[List[Any]], number_cols: set = frozenset()) -> None:
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for r in rows:
        ws.append([_safe(v) for v in r])
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(headers)):
        for cell in row:
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if cell.column in number_cols and isinstance(cell.value, (int, float)):
                cell.number_format = NUM_FMT
    for i, h in enumerate(headers, start=1):
        longest = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r) and r[i - 1] is not None])
        ws.column_dimensions[get_column_letter(i)].width = min(max(12, longest + 2), 55)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _kv_sheet(ws, pairs: List[tuple], number_fields: set = frozenset()) -> None:
    _write_table(ws, ["Field", "Value"], [[label, value] for label, value in pairs], number_cols={2} if number_fields else set())
    for row in range(2, ws.max_row + 1):
        ws.cell(row=row, column=1).font = Font(bold=True)


FIN_LABELS = [
    ("subtotal", "Subtotal"), ("taxable_amount", "Taxable amount"), ("discount", "Discount"),
    ("cgst", "CGST"), ("sgst", "SGST"), ("igst", "IGST"), ("other_charges", "Other charges"),
    ("shipping", "Shipping"), ("tax", "Total tax"), ("round_off", "Round-off"),
    ("grand_total", "Grand total"), ("amount_paid", "Amount paid"), ("balance_due", "Balance due"),
]


def export_excel(inv: InvoiceDetail) -> bytes:
    """Create a workbook whose first sheet immediately exposes the invoice contents.

    The first worksheet intentionally includes the line-item table and financial summary
    below the metadata. Detailed, filterable worksheets are retained for bookkeeping.
    """
    d = inv.data
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice Data"

    def section(row: int, title: str, headers: list[str] | None = None):
        ws.cell(row, 1, title)
        ws.cell(row, 1).font = Font(bold=True, color="FFFFFF", size=11)
        ws.cell(row, 1).fill = HEADER_FILL
        if headers:
            for col, value in enumerate(headers, 1):
                c = ws.cell(row + 1, col, value)
                c.font, c.fill, c.border = HEADER_FONT, HEADER_FILL, BORDER
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Invoice metadata, readable as a conventional two-column register.
    section(1, "INVOICE OVERVIEW", ["Field", "Value"])
    overview = [
        ("Invoice number", d.invoice_number), ("Invoice date", d.invoice_date),
        ("Due date", d.due_date), ("Purchase order number", d.purchase_order_number),
        ("Reference number", d.reference_number), ("Currency", d.currency),
        ("Invoice type", d.invoice_type), ("Payment terms", d.payment_terms),
        ("Source file", inv.original_filename), ("Extraction status", "Needs review" if inv.needs_review else "OK"),
        ("OCR / source", inv.ocr_engine), ("OCR mean confidence", inv.ocr_mean_confidence),
    ]
    for row, pair in enumerate(overview, 3):
        ws.cell(row, 1, _safe(pair[0])).font = Font(bold=True)
        ws.cell(row, 2, _safe(pair[1]))
        ws.cell(row, 1).border = ws.cell(row, 2).border = BORDER

    row = 17
    for title, party in (("SUPPLIER INFORMATION", d.supplier), ("CUSTOMER INFORMATION", d.customer)):
        section(row, title, ["Field", "Value"])
        for offset, (label, value) in enumerate([
            ("Name", party.name), ("Address", party.address), ("Email", party.email),
            ("Phone", party.phone), ("Tax ID / GSTIN", party.tax_id),
            ("Registration number", party.registration_number),
        ], 2):
            ws.cell(row + offset, 1, label).font = Font(bold=True)
            ws.cell(row + offset, 2, _safe(value))
            ws.cell(row + offset, 1).border = ws.cell(row + offset, 2).border = BORDER
        row += 10

    section(row, "INVOICE LINE ITEMS", ["Line no.", "Item code", "Description", "HSN/SAC", "Quantity", "Unit", "Unit price", "Discount", "Tax rate (%)", "Tax amount", "Line total"])
    item_header_row = row + 1
    for n, item in enumerate(d.items, 1):
        vals = [n, item.item_code, item.description, item.hsn_sac, item.quantity, item.unit,
                item.unit_price, item.discount, item.tax_rate, item.tax, item.total]
        ws.append([_safe(v) for v in vals])
        for cell in ws[ws.max_row]:
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if cell.column in {5, 7, 8, 10, 11} and isinstance(cell.value, (int, float)):
                cell.number_format = NUM_FMT
        if isinstance(item.tax_rate, (int, float)):
            ws.cell(ws.max_row, 9).number_format = '0.##"%"'
    row = ws.max_row + 3
    section(row, "FINANCIAL SUMMARY", ["Field", "Amount"])
    fin = d.financials.model_dump()
    for offset, (key, label) in enumerate(FIN_LABELS, 2):
        ws.cell(row + offset, 1, label).font = Font(bold=True)
        value = fin.get(key)
        ws.cell(row + offset, 2, value)
        ws.cell(row + offset, 1).border = ws.cell(row + offset, 2).border = BORDER
        if isinstance(value, (int, float)):
            ws.cell(row + offset, 2).number_format = NUM_FMT

    # Keep the detail sheets for users who need clean tables for downstream accounting.
    for title, party in (("Supplier", d.supplier), ("Customer", d.customer)):
        _kv_sheet(wb.create_sheet(title), [
            ("Name", party.name), ("Address", party.address), ("Email", party.email), ("Phone", party.phone),
            ("Tax ID / GSTIN", party.tax_id), ("Registration number", party.registration_number),
        ])
    items_ws = wb.create_sheet("Line Items")
    _write_table(items_ws,
        ["Line no.", "Item code", "Description", "HSN/SAC", "Quantity", "Unit", "Unit price", "Discount", "Tax rate (%)", "Tax amount", "Line total"],
        [[n, i.item_code, i.description, i.hsn_sac, i.quantity, i.unit, i.unit_price, i.discount, i.tax_rate, i.tax, i.total]
         for n, i in enumerate(d.items, start=1)], number_cols={5, 7, 8, 9, 10, 11})
    _kv_sheet(wb.create_sheet("Financials"), [(label, fin[k]) for k, label in FIN_LABELS])
    _write_table(wb.create_sheet("Validation"), ["Level", "Code", "Field", "Message"],
        [[i.level, i.code, i.field, i.message] for i in inv.validation.issues])
    _write_table(wb.create_sheet("Processing"), ["Stage", "Duration (ms)", "Duration (s)"],
        [[k, v, round(v / 1000, 3)] for k, v in inv.processing_times.items()], number_cols={2, 3})

    ws.freeze_panes = "A3"
    ws.sheet_view.showGridLines = False
    for col, width in {1: 28, 2: 34, 3: 54, 4: 16, 5: 13, 6: 12, 7: 16, 8: 14, 9: 15, 10: 16, 11: 16}.items():
        ws.column_dimensions[get_column_letter(col)].width = width
    for sheet in wb.worksheets[1:]:
        sheet.sheet_view.showGridLines = False
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def export_excel_consolidated(invoices: List[InvoiceDetail]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"
    headers = ["ID", "Invoice number", "Invoice date", "Due date", "Supplier", "Supplier tax ID", "Customer", "Customer tax ID", "Currency"] + [label for _, label in FIN_LABELS] + ["Status", "Source file"]
    rows = []
    for inv in invoices:
        d, f = inv.data, inv.data.financials.model_dump()
        rows.append([inv.id, d.invoice_number, d.invoice_date, d.due_date, d.supplier.name, d.supplier.tax_id,
                     d.customer.name, d.customer.tax_id, d.currency] + [f[k] for k, _ in FIN_LABELS] +
                    ["Needs review" if inv.needs_review else "OK", inv.original_filename])
    _write_table(ws, headers, rows, number_cols=set(range(10, 10 + len(FIN_LABELS))))

    iw = wb.create_sheet("Line Items")
    item_rows = []
    for inv in invoices:
        for n, i in enumerate(inv.data.items, start=1):
            item_rows.append([inv.id, inv.data.invoice_number, n, i.item_code, i.description, i.hsn_sac, i.quantity, i.unit,
                              i.unit_price, i.discount, i.tax_rate, i.tax, i.total])
    _write_table(iw, ["Invoice ID", "Invoice number", "#", "Item code", "Description", "HSN/SAC", "Quantity", "Unit", "Unit price", "Discount", "Tax rate %", "Tax", "Total"], item_rows, number_cols={7, 9, 10, 11, 12, 13})

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
