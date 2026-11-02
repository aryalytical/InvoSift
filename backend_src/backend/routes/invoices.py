from __future__ import annotations

import sqlite3
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from backend.config import get_settings
from backend.models import invoice as repo
from backend.schemas.invoice_schema import InvoiceDetail, InvoiceSummary, InvoiceUpdate
from backend.services import export_service
from backend.services.invoice_processor import ProcessingCancelled, ProcessingError, process_upload, revalidate_and_save
from backend.utils import file_utils
from backend.utils.file_utils import FileValidationError

router = APIRouter(prefix="/api", tags=["invoices"])
logger = logging.getLogger("invoice_ai.routes.invoices")
_progress: dict[str, dict] = {}
_cancel_events: dict[str, Event] = {}
_jobs_lock = Lock()
_extraction_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="invosift-extract")
_TERMINAL = {"complete", "cancelled", "error"}

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _db_guard(exc: Exception) -> HTTPException:
    return HTTPException(500, "A database error occurred. Please try again.")


def _must_get(invoice_id: int) -> InvoiceDetail:
    try:
        inv = repo.get_invoice(invoice_id)
    except sqlite3.Error as exc:
        raise _db_guard(exc)
    if inv is None:
        raise HTTPException(404, "Invoice not found.")
    return inv


def _download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _set_progress(token: str, value: dict) -> None:
    with _jobs_lock:
        # Never let a late worker callback overwrite a cancellation request.
        current = _progress.get(token, {})
        if current.get("stage") == "cancelling" and value.get("stage") not in _TERMINAL:
            return
        _progress[token] = value


def _run_extraction(token: str, filename: str, content: bytes, cancel_event: Event) -> None:
    try:
        result = process_upload(
            filename, content,
            progress_callback=lambda value: _set_progress(token, value),
            cancel_check=cancel_event.is_set,
        )
        if cancel_event.is_set():
            # A stop request received at the end must not report success.
            stored = repo.delete_invoice(result.id)
            if stored:
                file_utils.delete_stored_files(stored)
            _set_progress(token, {"stage": "cancelled", "percent": 0, "detail": "Extraction stopped. You can choose another file."})
            return
        _set_progress(token, {"stage": "complete", "percent": 100, "detail": "Extraction completed", "invoice_id": result.id})
    except ProcessingCancelled:
        _set_progress(token, {"stage": "cancelled", "percent": 0, "detail": "Extraction stopped. You can choose another file."})
    except ProcessingError as exc:
        _set_progress(token, {"stage": "error", "percent": None, "detail": exc.message, "invoice_id": exc.invoice_id})
    except sqlite3.Error:
        logger.exception("Database failure during invoice extraction (%s)", token)
        _set_progress(token, {"stage": "error", "percent": None, "detail": "A database error occurred while saving the invoice."})
    except Exception:
        logger.exception("Unexpected invoice extraction failure (%s)", token)
        _set_progress(token, {"stage": "error", "percent": None, "detail": "Extraction failed unexpectedly. Check the backend log and try again."})
    finally:
        with _jobs_lock:
            _cancel_events.pop(token, None)


@router.post("/invoices/upload", status_code=202)
def upload_invoice(file: UploadFile = File(...), x_progress_token: Optional[str] = Header(None)):
    """Validate and accept an upload, then run extraction outside the HTTP request."""
    s = get_settings()
    if not x_progress_token:
        raise HTTPException(400, "Missing extraction progress token. Please retry the upload.")

    with _jobs_lock:
        existing_event = _cancel_events.get(x_progress_token)
        if existing_event is not None and existing_event.is_set():
            _progress[x_progress_token] = {
                "stage": "cancelled", "percent": 0,
                "detail": "Extraction stopped before processing began.",
            }
            _cancel_events.pop(x_progress_token, None)
            try:
                file.file.close()
            except Exception:
                pass
            return {"token": x_progress_token, "stage": "cancelled"}
        if existing_event is not None:
            raise HTTPException(409, "An extraction with this token is already running.")
        cancel_event = Event()
        _cancel_events[x_progress_token] = cancel_event
        _progress[x_progress_token] = {
            "stage": "uploading", "percent": 2,
            "detail": "Receiving invoice file…",
        }

    try:
        content = file.file.read(s.max_upload_bytes + 1)
        if len(content) > s.max_upload_bytes:
            raise HTTPException(413, f"The file is too large. The maximum allowed size is {s.max_upload_mb} MB.")
        if not content:
            raise HTTPException(422, "The uploaded file is empty.")
        if cancel_event.is_set():
            _set_progress(x_progress_token, {
                "stage": "cancelled", "percent": 0, "detail": "Extraction stopped."
            })
            return {"token": x_progress_token, "stage": "cancelled"}

        _set_progress(x_progress_token, {
            "stage": "queued", "percent": 4,
            "detail": "Invoice received. Preparing document processing…",
        })
        try:
            _extraction_pool.submit(_run_extraction, x_progress_token, file.filename or "invoice", content, cancel_event)
        except RuntimeError as exc:
            _set_progress(x_progress_token, {
                "stage": "error", "percent": None,
                "detail": "The extraction worker is unavailable. Restart InvoSift and try again.",
            })
            raise HTTPException(503, "The extraction worker is unavailable. Please restart InvoSift.") from exc
        return {"token": x_progress_token, "stage": "queued"}
    except HTTPException as exc:
        # Always give the polling client a terminal state; never leave it at 2%/4%.
        _set_progress(x_progress_token, {
            "stage": "error", "percent": None, "detail": str(exc.detail),
        })
        with _jobs_lock:
            _cancel_events.pop(x_progress_token, None)
        raise
    except Exception as exc:
        logger.exception("Could not queue invoice extraction")
        _set_progress(x_progress_token, {
            "stage": "error", "percent": None,
            "detail": "The invoice upload could not be accepted by the extraction service.",
        })
        with _jobs_lock:
            _cancel_events.pop(x_progress_token, None)
        raise HTTPException(500, "The invoice could not be queued for extraction.") from exc
    finally:
        try:
            file.file.close()
        except Exception:
            pass


@router.get("/progress/{token}")
def invoice_progress(token: str):
    with _jobs_lock:
        return dict(_progress.get(token, {"stage": "starting", "percent": 1, "detail": "Waiting for upload…"}))


@router.post("/invoices/cancel/{token}", status_code=202)
def cancel_invoice_extraction(token: str):
    with _jobs_lock:
        state = _progress.get(token, {})
        if state.get("stage") in _TERMINAL:
            return {"ok": False, "stage": state.get("stage")}
        # Create the event even if the multipart upload has not reached the
        # upload handler yet. The handler will observe this pre-cancelled token.
        event = _cancel_events.setdefault(token, Event())
        event.set()
        _progress[token] = {"stage": "cancelling", "percent": None, "detail": "Stop requested. Cancelling the active processing stage…"}
    return {"ok": True, "stage": "cancelling"}


@router.get("/invoices", response_model=list[InvoiceSummary])
def list_invoices(search: Optional[str] = Query(None, max_length=100), status: Optional[str] = Query(None)):
    try:
        return repo.list_invoices(search, status)
    except sqlite3.Error as exc:
        raise _db_guard(exc)


@router.get("/stats")
def stats():
    try:
        return repo.dashboard_stats()
    except sqlite3.Error as exc:
        raise _db_guard(exc)


# NOTE: static "export" routes are declared before the {invoice_id} routes for clarity.
@router.get("/invoices/export/excel")
def export_all_excel():
    """Consolidated Excel report for all stored invoices."""
    try:
        summaries = repo.list_invoices()
        full = [i for i in (repo.get_invoice(x.id) for x in summaries) if i and i.status != "failed"]
    except sqlite3.Error as exc:
        raise _db_guard(exc)
    if not full:
        raise HTTPException(404, "There are no invoices to export.")
    return _download(export_service.export_excel_consolidated(full), XLSX, "invoices_consolidated.xlsx")


@router.get("/invoices/export/csv")
def export_all_csv():
    try:
        summaries = repo.list_invoices()
        full = [i for i in (repo.get_invoice(x.id) for x in summaries) if i and i.status != "failed"]
    except sqlite3.Error as exc:
        raise _db_guard(exc)
    if not full:
        raise HTTPException(404, "There are no invoices to export.")
    return _download(export_service.export_csv(full), "text/csv; charset=utf-8", "invoices_all.csv")


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: int):
    return _must_get(invoice_id)


@router.put("/invoices/{invoice_id}", response_model=InvoiceDetail)
def update_invoice(invoice_id: int, body: InvoiceUpdate):
    _must_get(invoice_id)
    try:
        updated = revalidate_and_save(invoice_id, body.data, body.mark_reviewed)
    except sqlite3.Error as exc:
        raise _db_guard(exc)
    if updated is None:
        raise HTTPException(404, "Invoice not found.")
    return updated


@router.delete("/invoices/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: int):
    try:
        stored = repo.delete_invoice(invoice_id)
    except sqlite3.Error as exc:
        raise _db_guard(exc)
    if stored is None:
        raise HTTPException(404, "Invoice not found.")
    file_utils.delete_stored_files(stored)
    return Response(status_code=204)


# ----------------------------------------------------------------------------- preview (by id only)

@router.get("/invoices/{invoice_id}/file")
def original_file(invoice_id: int):
    """The original upload, for the preview pane. Looked up by id; no path is ever taken from the client."""
    row = repo.get_raw_row(invoice_id)
    if row is None:
        raise HTTPException(404, "Invoice not found.")
    try:
        path = file_utils.resolve_stored_file(row["stored_filename"])
    except FileValidationError:
        raise HTTPException(400, "Invalid file.")
    if not path.exists():
        raise HTTPException(404, "The original file is no longer available.")
    return FileResponse(path, media_type=row["mime_type"] or "application/octet-stream",
                        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})


@router.get("/invoices/{invoice_id}/page/{page}")
def page_image(invoice_id: int, page: int):
    """Rendered page image (JPEG) - used to preview every page of a PDF."""
    row = repo.get_raw_row(invoice_id)
    if row is None:
        raise HTTPException(404, "Invoice not found.")
    if page < 1 or page > row["page_count"]:
        raise HTTPException(404, "Page not found.")
    stem = file_utils.resolve_stored_file(row["stored_filename"]).stem
    path = get_settings().processed_dir / f"{stem}_p{page}.jpg"
    if not path.exists():
        raise HTTPException(404, "Preview not available.")
    return FileResponse(path, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff"})


# ----------------------------------------------------------------------------- export (one invoice)

@router.get("/invoices/{invoice_id}/export/json")
def export_json(invoice_id: int):
    inv = _must_get(invoice_id)
    return _download(export_service.export_json(inv), "application/json", f"invoice_{invoice_id}.json")


@router.get("/invoices/{invoice_id}/export/csv")
def export_csv(invoice_id: int):
    inv = _must_get(invoice_id)
    return _download(export_service.export_csv([inv]), "text/csv; charset=utf-8", f"invoice_{invoice_id}.csv")


@router.get("/invoices/{invoice_id}/export/excel")
def export_excel(invoice_id: int):
    inv = _must_get(invoice_id)
    return _download(export_service.export_excel(inv), XLSX, f"invoice_{invoice_id}.xlsx")


@router.get("/invoices/{invoice_id}/export/text")
def export_text(invoice_id: int):
    """Human-readable, copy-ready invoice report with explicit missing-value labels."""
    inv = _must_get(invoice_id)
    d, f = inv.data, inv.data.financials
    missing = lambda v: v if v not in (None, "") else "Not provided"
    lines = [
        "INVOICE OVERVIEW",
        f"Invoice Number: {missing(d.invoice_number)}",
        f"Invoice Date: {missing(d.invoice_date)}",
        f"Due Date: {missing(d.due_date)}",
        f"PO Number: {missing(d.purchase_order_number)}",
        f"Reference: {missing(d.reference_number)}",
        f"Invoice Type: {missing(d.invoice_type)}",
        f"Currency: {missing(d.currency)}",
        f"Payment Terms: {missing(d.payment_terms)}",
        "", "SUPPLIER INFORMATION",
        f"Supplier Name: {missing(d.supplier.name)}",
        f"Supplier Address: {missing(d.supplier.address)}",
        f"Supplier Email: {missing(d.supplier.email)}",
        f"Supplier Phone: {missing(d.supplier.phone)}",
        f"Supplier Tax ID / GSTIN: {missing(d.supplier.tax_id)}",
        f"Supplier Registration Number: {missing(d.supplier.registration_number)}",
        "", "CUSTOMER / BILL-TO INFORMATION",
        f"Customer Name: {missing(d.customer.name)}",
        f"Customer Address: {missing(d.customer.address)}",
        f"Customer Email: {missing(d.customer.email)}",
        f"Customer Phone: {missing(d.customer.phone)}",
        f"Customer Tax ID / GSTIN: {missing(d.customer.tax_id)}",
        f"Customer Registration Number: {missing(d.customer.registration_number)}",
        "", f"INVOICE ITEMS ({len(d.items)} rows)",
    ]
    for n, item in enumerate(d.items, 1):
        lines.extend([
            f"{n}. {missing(item.description)}",
            f"   Item code: {missing(item.item_code)} | HSN/SAC: {missing(item.hsn_sac)}",
            f"   Quantity: {missing(item.quantity)} {item.unit or ''}".rstrip(),
            f"   Unit price: {missing(item.unit_price)}",
            f"   Discount: {missing(item.discount)}",
            f"   Tax rate: {missing(item.tax_rate)}%" if item.tax_rate is not None else "   Tax rate: Not provided",
            f"   Tax amount: {missing(item.tax)}",
            f"   Line total: {missing(item.total)}",
        ])
    lines.extend(["", "FINANCIAL SUMMARY"])
    for label, value in [
        ("Subtotal", f.subtotal), ("Taxable amount", f.taxable_amount), ("Discount", f.discount),
        ("CGST", f.cgst), ("SGST", f.sgst), ("IGST", f.igst), ("Other charges", f.other_charges),
        ("Shipping", f.shipping), ("Total tax", f.tax), ("Round off", f.round_off),
        ("Grand total", f.grand_total), ("Amount paid", f.amount_paid), ("Balance due", f.balance_due),
    ]:
        lines.append(f"{label}: {missing(value)}")
    lines.extend(["", "VALIDATION & REVIEW", f"Status: {'Needs review' if inv.needs_review else 'No validation warnings'}"])
    if inv.validation.issues:
        for issue in inv.validation.issues:
            lines.append(f"- [{issue.level.upper()}] {issue.message}")
    else:
        lines.append("- No validation issues were recorded.")
    lines.extend(["", "IMPORTANT NOTES", "- 'Not provided' means the value was not confidently found in the source document.",
                  "- Verify extracted values against the original invoice before accounting, payment, or tax filing.",
                  f"- Source file: {inv.original_filename}"])
    return _download("\n".join(lines).encode("utf-8"), "text/plain; charset=utf-8", f"invoice_{invoice_id}.txt")
