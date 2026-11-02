"""Data-access layer: maps InvoiceData <-> SQLite rows."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.database import get_db
from backend.schemas.invoice_schema import (
    InvoiceData,
    InvoiceDetail,
    InvoiceSummary,
    ValidationReport,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _flatten(data: InvoiceData) -> Dict[str, Any]:
    f = data.financials
    return {
        "invoice_number": data.invoice_number,
        "invoice_date": data.invoice_date,
        "due_date": data.due_date,
        "purchase_order_number": data.purchase_order_number,
        "reference_number": data.reference_number,
        "currency": data.currency,
        "invoice_type": data.invoice_type,
        "payment_terms": data.payment_terms,
        "supplier_name": data.supplier.name,
        "supplier_address": data.supplier.address,
        "supplier_email": data.supplier.email,
        "supplier_phone": data.supplier.phone,
        "supplier_tax_id": data.supplier.tax_id,
        "supplier_registration_number": data.supplier.registration_number,
        "customer_name": data.customer.name,
        "customer_address": data.customer.address,
        "customer_email": data.customer.email,
        "customer_phone": data.customer.phone,
        "customer_tax_id": data.customer.tax_id,
        "customer_registration_number": data.customer.registration_number,
        "subtotal": f.subtotal, "taxable_amount": f.taxable_amount, "discount": f.discount, "cgst": f.cgst, "sgst": f.sgst,
        "igst": f.igst, "other_charges": f.other_charges, "shipping": f.shipping,
        "tax": f.tax, "round_off": f.round_off, "grand_total": f.grand_total, "amount_paid": f.amount_paid,
        "balance_due": f.balance_due,
    }


def _row_to_data(row: sqlite3.Row, items: List[sqlite3.Row]) -> InvoiceData:
    r = dict(row)
    return InvoiceData.model_validate(
        {
            "invoice_number": r["invoice_number"],
            "invoice_date": r["invoice_date"],
            "due_date": r["due_date"],
            "purchase_order_number": r["purchase_order_number"],
            "reference_number": r["reference_number"],
            "currency": r["currency"],
            "invoice_type": r["invoice_type"],
            "payment_terms": r["payment_terms"],
            "supplier": {
                "name": r["supplier_name"], "address": r["supplier_address"], "email": r["supplier_email"],
                "phone": r["supplier_phone"], "tax_id": r["supplier_tax_id"],
                "registration_number": r["supplier_registration_number"],
            },
            "customer": {
                "name": r["customer_name"], "address": r["customer_address"], "email": r["customer_email"],
                "phone": r["customer_phone"], "tax_id": r["customer_tax_id"],
                "registration_number": r["customer_registration_number"],
            },
            "items": [
                {k: i[k] for k in ("item_code", "description", "hsn_sac", "quantity", "unit", "unit_price", "discount", "tax_rate", "tax", "total")}
                for i in items
            ],
            "financials": {
                k: r[k] for k in (
                    "subtotal", "taxable_amount", "discount", "cgst", "sgst", "igst", "other_charges", "shipping",
                    "tax", "round_off", "grand_total", "amount_paid", "balance_due",
                )
            },
        }
    )


def _write_items(conn: sqlite3.Connection, invoice_id: int, data: InvoiceData) -> None:
    conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))
    conn.executemany(
        "INSERT INTO invoice_items (invoice_id, line_no, item_code, description, hsn_sac, quantity, unit, unit_price, discount, tax_rate, tax, total)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (invoice_id, n, it.item_code, it.description, it.hsn_sac, it.quantity, it.unit, it.unit_price, it.discount, it.tax_rate, it.tax, it.total)
            for n, it in enumerate(data.items, start=1)
        ],
    )


# ----------------------------------------------------------------------------- create / update

def create_invoice(original_filename: str, stored_filename: str, mime_type: str, page_count: int) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO invoices (original_filename, stored_filename, mime_type, page_count, status, created_at)"
            " VALUES (?,?,?,?, 'processing', ?)",
            (original_filename, stored_filename, mime_type, page_count, now_iso()),
        )
        return int(cur.lastrowid)


def save_extraction(
    invoice_id: int,
    data: InvoiceData,
    report: ValidationReport,
    ocr_engine: Optional[str],
    ocr_mean_confidence: Optional[float],
    raw_ai_json: Optional[str],
) -> None:
    status = "needs_review" if report.needs_review else "completed"
    cols = _flatten(data)
    assignments = ", ".join(f"{k} = ?" for k in cols)
    with get_db() as conn:
        conn.execute(
            f"UPDATE invoices SET {assignments}, status = ?, validation_status = ?, validation_json = ?,"
            " raw_ai_json = ?, ocr_engine = ?, ocr_mean_confidence = ?, error_message = NULL, updated_at = ?"
            " WHERE id = ?",
            (
                *cols.values(), status, "needs_review" if report.needs_review else "valid",
                report.model_dump_json(), raw_ai_json, ocr_engine, ocr_mean_confidence, now_iso(), invoice_id,
            ),
        )
        _write_items(conn, invoice_id, data)


def mark_failed(invoice_id: int, message: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE invoices SET status = 'failed', error_message = ?, updated_at = ? WHERE id = ?",
            (message, now_iso(), invoice_id),
        )


def update_invoice(invoice_id: int, data: InvoiceData, report: ValidationReport, reviewed: bool) -> bool:
    """Persist manual corrections. Status reflects the re-validation unless the user marked it reviewed."""
    cols = _flatten(data)
    assignments = ", ".join(f"{k} = ?" for k in cols)
    status = "completed" if (reviewed or not report.needs_review) else "needs_review"
    with get_db() as conn:
        cur = conn.execute(
            f"UPDATE invoices SET {assignments}, status = ?, validation_status = ?, validation_json = ?,"
            " manually_edited = 1, updated_at = ? WHERE id = ?",
            (
                *cols.values(), status, "needs_review" if report.needs_review else "valid",
                report.model_dump_json(), now_iso(), invoice_id,
            ),
        )
        if cur.rowcount == 0:
            return False
        _write_items(conn, invoice_id, data)
        return True


def add_log(invoice_id: Optional[int], stage: str, message: str, level: str = "info",
            duration_ms: Optional[int] = None) -> None:
    """Processing logs never contain invoice contents - only stage names, counts and timings."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO processing_logs (invoice_id, stage, level, message, duration_ms, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (invoice_id, stage, level, message[:500], duration_ms, now_iso()),
            )
    except Exception:  # logging must never break processing
        pass


# ----------------------------------------------------------------------------- read

def _summary(row: sqlite3.Row) -> InvoiceSummary:
    return InvoiceSummary(
        id=row["id"], invoice_number=row["invoice_number"], supplier_name=row["supplier_name"],
        invoice_date=row["invoice_date"], grand_total=row["grand_total"], currency=row["currency"],
        status=row["status"], needs_review=row["status"] in ("needs_review", "failed"),
        original_filename=row["original_filename"], created_at=row["created_at"],
    )


def list_invoices(search: Optional[str] = None, status: Optional[str] = None) -> List[InvoiceSummary]:
    sql = "SELECT * FROM invoices"
    where, params = [], []
    if search:
        where.append("(invoice_number LIKE ? OR supplier_name LIKE ? OR customer_name LIKE ? OR original_filename LIKE ?)")
        params += [f"%{search}%"] * 4
    if status:
        where.append("status = ?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    with get_db() as conn:
        return [_summary(r) for r in conn.execute(sql, params).fetchall()]


def get_raw_row(invoice_id: int) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()


def get_invoice(invoice_id: int) -> Optional[InvoiceDetail]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        items = conn.execute(
            "SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY line_no", (invoice_id,)
        ).fetchall()
    data = _row_to_data(row, items)
    try:
        report = ValidationReport.model_validate_json(row["validation_json"]) if row["validation_json"] else ValidationReport()
    except Exception:
        report = ValidationReport()
    base = _summary(row)
    return InvoiceDetail(
        **base.model_dump(), data=data, validation=report, page_count=row["page_count"],
        ocr_engine=row["ocr_engine"], ocr_mean_confidence=row["ocr_mean_confidence"],
        error_message=row["error_message"], updated_at=row["updated_at"],
        manually_edited=bool(row["manually_edited"]),
        processing_times=get_processing_times(invoice_id),
    )


def get_processing_times(invoice_id: int) -> Dict[str, int]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT stage, duration_ms FROM processing_logs WHERE invoice_id = ? AND duration_ms IS NOT NULL",
            (invoice_id,),
        ).fetchall()
    return {str(r["stage"]): int(r["duration_ms"]) for r in rows}


def delete_invoice(invoice_id: int) -> Optional[str]:
    """Delete the DB record (items/logs cascade). Returns the stored filename so the caller can remove the file."""
    with get_db() as conn:
        row = conn.execute("SELECT stored_filename FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        return row["stored_filename"]


def dashboard_stats() -> Dict[str, Any]:
    today = datetime.now().astimezone().date().isoformat()
    with get_db() as conn:
        r = conn.execute(
            "SELECT COUNT(*) total,"
            " SUM(CASE WHEN substr(created_at,1,10) = ? THEN 1 ELSE 0 END) today,"
            " SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) ok,"
            " SUM(CASE WHEN status IN ('needs_review','failed') THEN 1 ELSE 0 END) review,"
            " SUM(CASE WHEN status != 'failed' THEN COALESCE(grand_total,0) ELSE 0 END) value"
            " FROM invoices",
            (today,),
        ).fetchone()
    return {
        "total_invoices": r["total"] or 0,
        "processed_today": r["today"] or 0,
        "successful_extractions": r["ok"] or 0,
        "needs_review": r["review"] or 0,
        "total_invoice_value": round(r["value"] or 0, 2),
    }
