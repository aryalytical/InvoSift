from io import BytesIO

from openpyxl import load_workbook

from backend.schemas.invoice_schema import InvoiceData, InvoiceDetail, ValidationReport
from backend.services.deterministic_extractor import reconcile
from backend.services.export_service import export_csv, export_excel, export_json

SAMPLE = '''Invoice number: INV-2026-1048
Invoice date: 05 October 2026
Due date: 04 November 2026
Currency: INR
Subtotal: 43500.00
CGST: 3915.00
SGST: 3915.00
Grand Total: 51330.00
| line_no | description | quantity | unit | unit_price | tax_amount | tax_rate | line_total |
| 1 | Cloud document processing — Professional Plan | 1 | | 18500.00 | 3330.00 | 18% | 18500.00 |
| 2 | Invoice OCR & data extraction module | 2 | | 6750.00 | 2430.00 | 18% | 13500.00 |
| 3 | Implementation and configuration services | 1 | | 8000.00 | 1440.00 | 18% | 8000.00 |
| 4 | Technical support — 30 days | 1 | | 3500.00 | 630.00 | 18% | 3500.00 |'''


def test_reconciles_line_item_tax_and_financial_fields():
    data = reconcile(InvoiceData(), SAMPLE)
    assert data.invoice_number == "INV-2026-1048"
    assert data.invoice_date == "05 October 2026"
    assert data.due_date == "04 November 2026"
    assert data.currency == "INR"
    assert len(data.items) == 4
    assert [item.tax for item in data.items] == [3330, 2430, 1440, 630]
    assert [item.total for item in data.items] == [18500, 13500, 8000, 3500]
    assert data.financials.grand_total == 51330


def test_excel_first_sheet_contains_line_items_and_financials():
    data = reconcile(InvoiceData(), SAMPLE)
    invoice = InvoiceDetail(
        id=1, invoice_number=data.invoice_number, supplier_name=None,
        invoice_date=data.invoice_date, grand_total=data.financials.grand_total,
        currency="INR", status="completed", needs_review=False,
        original_filename="high_quality_sample_invoice.pdf", created_at="2026-11-01",
        data=data, validation=ValidationReport(), page_count=1,
    )
    workbook = load_workbook(BytesIO(export_excel(invoice)), data_only=True)
    assert workbook.sheetnames[0] == "Invoice Data"
    assert {"Line Items", "Supplier", "Customer", "Financials", "Validation"}.issubset(workbook.sheetnames)
    rows = list(workbook["Invoice Data"].iter_rows(values_only=True))
    assert any(row[2] == "Cloud document processing — Professional Plan" and row[9] == 3330 for row in rows if len(row) > 9)
    assert any(row[0] == "Grand total" and row[1] == 51330 for row in rows if len(row) > 1)
    assert len(export_json(invoice)) > 100
    assert len(export_csv([invoice])) > 100
