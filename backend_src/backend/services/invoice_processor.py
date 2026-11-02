"""Fast local invoice processing pipeline."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from backend.config import get_settings
from backend.models import invoice as repo
from backend.schemas.invoice_schema import InvoiceDetail
from backend.services import ai_extractor, ocr_service, validation_service, deterministic_extractor
from backend.services.ai_extractor import AIExtractionError, ExtractionCancelled
from backend.services.ocr_service import OCRPage, OCRResult, OCRLine, OCRError
from backend.utils import file_utils, image_utils
from backend.utils.file_utils import FileValidationError
from backend.utils.image_utils import DocumentReadError

logger = logging.getLogger("invoice_ai.processor")


class ProcessingCancelled(Exception):
    """Internal cooperative cancellation signal for an extraction request."""


class ProcessingError(Exception):
    def __init__(self, message: str, status_code: int = 422, invoice_id: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.invoice_id = invoice_id


def _native_text_result(page_texts: list[str]) -> OCRResult:
    pages: list[OCRPage] = []
    for page_no, text in enumerate(page_texts, start=1):
        lines: list[OCRLine] = []
        for row_no, raw in enumerate(text.splitlines()):
            line = raw.strip()
            if not line:
                continue
            # Native PDF text has no OCR confidence/bounding boxes. The synthetic box only
            # preserves reading order for the existing row reconstruction logic.
            lines.append(OCRLine(line, 1.0, [0.0, float(row_no), 10000.0, float(row_no + 1)]))
        pages.append(OCRPage(page=page_no, lines=lines))
    return OCRResult(engine="pdf-text", pages=pages)


def process_upload(filename: str, content: bytes, progress_callback=None, cancel_check=None) -> InvoiceDetail:
    s = get_settings()
    s.ensure_dirs()
    def progress(stage: str, percent: int, detail: str):
        if progress_callback:
            try: progress_callback({"stage": stage, "percent": percent, "detail": detail})
            except Exception: pass
    def check_cancelled():
        if cancel_check and cancel_check():
            raise ProcessingCancelled("Extraction stopped by user.")

    progress("received", 4, "File received and queued")
    check_cancelled()

    try:
        ext, mime = file_utils.validate_upload(filename, content)
    except FileValidationError as exc:
        raise ProcessingError(exc.message, exc.status_code) from exc

    try:
        ai_extractor.ensure_ready()
        check_cancelled()
    except AIExtractionError as exc:
        raise ProcessingError(exc.message, exc.status_code) from exc

    path = file_utils.save_upload(content, ext)
    display_name = file_utils.sanitize_filename(filename)
    invoice_id: Optional[int] = None

    def stage(name: str, started: float, msg: str = "ok") -> None:
        repo.add_log(invoice_id, name, msg, duration_ms=int((time.perf_counter() - started) * 1000))

    total_started = time.perf_counter()
    try:
        progress("reading", 10, "Checking document structure")
        check_cancelled()
        # 1. Read native PDF text before rendering/OCR. Digital PDFs can skip OCR entirely.
        native_texts: Optional[list[str]] = None
        native_text_elapsed_ms: Optional[int] = None
        if ext == ".pdf":
            t = time.perf_counter()
            try:
                try:
                    candidate = image_utils.extract_native_pdf_text(path, cancel_check=cancel_check)
                except RuntimeError as cancel_exc:
                    if str(cancel_exc) == "__CANCELLED__": raise ProcessingCancelled("Extraction stopped by user.") from cancel_exc
                    raise
                if image_utils.native_pdf_text_is_usable(candidate):
                    native_texts = candidate
                    logger.info("Usable native PDF text found; OCR will be skipped")
                else:
                    logger.info("PDF has no sufficiently useful text layer; OCR required")
            except DocumentReadError:
                native_texts = None
            native_text_elapsed_ms = int((time.perf_counter() - t) * 1000)

        # 2. Render pages for preview and for OCR/vision when needed.
        t = time.perf_counter()
        def render_progress(done, total):
            percent = 12 + int(12 * done / max(total, 1))
            progress("render", percent, f"Rendered page {done} of {total}")
        try:
            pages = image_utils.load_document_pages(path, cancel_check=cancel_check, progress_callback=render_progress)
        except ProcessingCancelled:
            raise
        except RuntimeError as exc:
            if str(exc) == "__CANCELLED__": raise ProcessingCancelled("Extraction stopped by user.") from exc
            raise
        except DocumentReadError as exc:
            raise ProcessingError(str(exc), 422) from exc
        check_cancelled()
        invoice_id = repo.create_invoice(display_name, path.name, mime, len(pages))
        if native_text_elapsed_ms is not None:
            repo.add_log(invoice_id, "pdf_text", "usable" if native_texts is not None else "ocr_required", duration_ms=native_text_elapsed_ms)
        stage("render", t, f"{len(pages)} page(s)")
        progress("render", 24, f"Rendered {len(pages)} page(s)")

        try:
            image_utils.save_page_previews(pages, path.stem, cancel_check=cancel_check)
        except RuntimeError as cancel_exc:
            if str(cancel_exc) == "__CANCELLED__": raise ProcessingCancelled("Extraction stopped by user.") from cancel_exc
            raise
        check_cancelled()
        if all(image_utils.is_blank(p) for p in pages):
            raise ProcessingError("The document appears to be blank. Please upload a readable invoice.", 422, invoice_id)

        progress("ocr", 34, "Reading text from the document")
        # 3. OCR only when the PDF does not already provide usable text.
        if native_texts is not None:
            ocr = _native_text_result(native_texts)
            stage("ocr", time.perf_counter(), "skipped - native PDF text")
        else:
            t = time.perf_counter()
            try:
                def ocr_progress(done, total, chars):
                    if done <= 0:
                        progress("ocr", 35, "Initialising the local OCR engine for image recognition")
                    else:
                        percent = 34 + int(22 * done / max(total, 1))
                        progress("ocr", percent, f"Recognised page {done} of {total} · {chars} characters found")
                ocr = ocr_service.run_ocr(pages, cancel_check=cancel_check, progress_callback=ocr_progress)
            except RuntimeError as exc:
                if str(exc) == "__CANCELLED__": raise ProcessingCancelled("Extraction stopped by user.") from exc
                raise
            except OCRError as exc:
                raise ProcessingError(str(exc), 500, invoice_id) from exc
            stage("ocr", t, f"{ocr.engine}, {ocr.char_count} chars")
        check_cancelled()
        progress("ocr", 56, f"Text recognition complete · {ocr.char_count} characters")

        if ocr.char_count < 15:
            raise ProcessingError(
                "Very little readable text was found. Check that the invoice is not blank, encrypted or severely blurred.",
                422, invoice_id,
            )

        progress("ai_extraction", 62, "Identifying invoice fields and line items")
        # 4. Structured AI extraction. OCR text is the default for both scanned PDFs
        # and image invoices. Optional image-assisted extraction remains available through
        # OLLAMA_USE_IMAGE=true, but is disabled by default because local 4B vision inference
        # can be extremely slow on CPU/shared-memory machines.
        t = time.perf_counter()
        use_images = ocr.engine != "pdf-text" and s.ollama_use_image
        images_b64 = []
        if use_images:
            images_b64 = [image_utils.image_to_base64_jpeg(p) for p in pages[: s.ollama_max_images]]
        try:
            def ai_progress(chars):
                # Streaming output is real model output; the percentage advances
                # within the AI stage without pretending to know model token timing.
                percent = min(82, 62 + int(chars / 220))
                progress("ai_extraction", percent, f"AI model is returning structured data · {chars:,} characters received")
            data, raw_json = ai_extractor.extract_invoice(ocr.text, images_b64, use_images=use_images,
                                                          cancel_check=cancel_check, progress_callback=ai_progress)
            check_cancelled()
            data = deterministic_extractor.reconcile(data, ocr.text)
            raw_json = data.model_dump_json()
        except AIExtractionError as exc:
            raise ProcessingError(exc.message, exc.status_code, invoice_id) from exc
        stage("ai_extraction", t, f"model={s.ollama_model}, images={'yes' if use_images else 'no'}")
        progress("ai_extraction", 84, "Structured fields and line items extracted")

        progress("validation", 90, "Checking totals, dates, and required fields")
        check_cancelled()
        # 5. Deterministic validation.
        t = time.perf_counter()
        report = validation_service.validate_invoice(
            data,
            ocr_text=ocr.text,
            ocr_mean_confidence=None if ocr.engine == "pdf-text" else ocr.mean_confidence,
            ocr_low_ratio=0.0 if ocr.engine == "pdf-text" else ocr.low_confidence_ratio,
        )
        stage("validation", t, "needs_review" if report.needs_review else "valid")
        progress("save", 96, "Saving extracted information and audit details")

        # 6. Persist.
        t = time.perf_counter()
        check_cancelled()
        repo.save_extraction(invoice_id, data, report, ocr.engine, None if ocr.engine == "pdf-text" else ocr.mean_confidence, raw_json)
        stage("save", t)
        check_cancelled()
        repo.add_log(invoice_id, "total", "completed", duration_ms=int((time.perf_counter() - total_started) * 1000))

        # The route/job manager publishes the terminal `complete` state only after
        # this function returns the saved invoice id. Publishing it here creates a
        # race where the UI sees `complete` before `invoice_id` is attached.
        progress("save", 99, "Finalising the saved invoice record")
        detail = repo.get_invoice(invoice_id)
        assert detail is not None
        return detail

    except (ProcessingCancelled, ExtractionCancelled):
        if invoice_id is not None:
            stored = repo.delete_invoice(invoice_id)
            if stored:
                file_utils.delete_stored_files(stored)
        else:
            path.unlink(missing_ok=True)
        raise ProcessingCancelled("Extraction stopped. You can choose another file.")
    except ProcessingError as exc:
        if invoice_id is not None:
            repo.mark_failed(invoice_id, exc.message)
            repo.add_log(invoice_id, "failed", exc.message, level="error")
            exc.invoice_id = invoice_id
        else:
            path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logger.exception("Unexpected processing failure")
        if invoice_id is not None:
            repo.mark_failed(invoice_id, "Unexpected error while processing the invoice.")
        else:
            path.unlink(missing_ok=True)
        raise ProcessingError("An unexpected error occurred while processing the invoice.", 500, invoice_id) from exc


def revalidate_and_save(invoice_id: int, data, mark_reviewed: bool = True) -> Optional[InvoiceDetail]:
    report = validation_service.validate_invoice(data)
    if not repo.update_invoice(invoice_id, data, report, reviewed=mark_reviewed):
        return None
    repo.add_log(invoice_id, "manual_edit", "values corrected by user")
    return repo.get_invoice(invoice_id)
