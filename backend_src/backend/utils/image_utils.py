"""Document loading, native PDF text extraction, rendering and OCR preprocessing."""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

from backend.config import get_settings


class DocumentReadError(Exception):
    """The document could not be opened."""


def pil_to_bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def load_image(path: Path) -> Image.Image:
    try:
        img = Image.open(path)
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception as exc:
        raise DocumentReadError("The image could not be read. It may be corrupted.") from exc
    if img.width < 10 or img.height < 10:
        raise DocumentReadError("The image is too small to contain an invoice.")
    return img.convert("RGB")


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(str(path))
        try:
            return len(pdf)
        finally:
            pdf.close()
    except Exception as exc:
        raise DocumentReadError("The PDF could not be opened. It may be corrupted or password protected.") from exc


def extract_native_pdf_text(path: Path, cancel_check=None) -> List[str]:
    """Extract selectable PDF text without rendering/OCR.

    Returns one string per page. An empty list means the PDF has no usable text layer.
    This is the main performance optimization for digitally generated invoices.
    """
    import pypdfium2 as pdfium

    s = get_settings()
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise DocumentReadError("The PDF could not be opened. It may be corrupted or password protected.") from exc

    pages: List[str] = []
    try:
        n = len(pdf)
        if n == 0:
            raise DocumentReadError("The PDF has no pages.")
        if n > s.max_pdf_pages:
            raise DocumentReadError(f"The PDF has {n} pages. The maximum supported is {s.max_pdf_pages} pages.")
        for i in range(n):
            if cancel_check and cancel_check(): raise RuntimeError("__CANCELLED__")
            page = pdf[i]
            try:
                textpage = page.get_textpage()
                text = textpage.get_text_range() or ""
                pages.append(text.replace("\x00", "").strip())
            finally:
                page.close()
    except DocumentReadError:
        raise
    except RuntimeError as exc:
        if str(exc) == "__CANCELLED__": raise
        raise DocumentReadError("The PDF text layer could not be read.") from exc
    except Exception as exc:
        raise DocumentReadError("The PDF text layer could not be read.") from exc
    finally:
        pdf.close()
    return pages


def native_pdf_text_is_usable(page_texts: List[str]) -> bool:
    """Conservative heuristic: only skip OCR when the text layer looks real."""
    text = "\n".join(t for t in page_texts if t).strip()
    if len(text) < 40:
        return False
    alnum = sum(ch.isalnum() for ch in text)
    if alnum < 25 or alnum / max(len(text), 1) < 0.30:
        return False
    lowered = text.lower()
    invoice_hints = ("invoice", "bill", "tax invoice", "subtotal", "total", "amount", "gst", "date", "qty", "quantity")
    return sum(1 for hint in invoice_hints if hint in lowered) >= 1


def render_pdf_pages(path: Path, cancel_check=None, progress_callback=None) -> List[Image.Image]:
    import pypdfium2 as pdfium

    s = get_settings()
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise DocumentReadError("The PDF could not be opened. It may be corrupted or password protected.") from exc
    pages: List[Image.Image] = []
    try:
        n = len(pdf)
        if n == 0:
            raise DocumentReadError("The PDF has no pages.")
        if n > s.max_pdf_pages:
            raise DocumentReadError(f"The PDF has {n} pages. The maximum supported is {s.max_pdf_pages} pages.")
        for i in range(n):
            if cancel_check and cancel_check(): raise RuntimeError("__CANCELLED__")
            page = pdf[i]
            try:
                bitmap = page.render(scale=s.pdf_render_scale)
                pages.append(bitmap.to_pil().convert("RGB"))
                if progress_callback:
                    progress_callback(i + 1, n)
            finally:
                page.close()
    except DocumentReadError:
        raise
    except RuntimeError as exc:
        if str(exc) == "__CANCELLED__": raise
        raise DocumentReadError("A page of the PDF could not be rendered. The file may be corrupted.") from exc
    except Exception as exc:
        raise DocumentReadError("A page of the PDF could not be rendered. The file may be corrupted.") from exc
    finally:
        pdf.close()
    return pages


def load_document_pages(path: Path, cancel_check=None, progress_callback=None) -> List[Image.Image]:
    if path.suffix.lower() == ".pdf":
        return render_pdf_pages(path, cancel_check=cancel_check, progress_callback=progress_callback)
    if cancel_check and cancel_check(): raise RuntimeError("__CANCELLED__")
    image = load_image(path)
    if progress_callback:
        progress_callback(1, 1)
    return [image]


def resize_if_needed(img: Image.Image) -> Image.Image:
    max_side = get_settings().max_image_side
    w, h = img.size
    longest = max(w, h)
    if longest > max_side:
        scale = max_side / longest
        return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    if longest < 1000:
        scale = 1000 / longest
        return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def preprocess_for_ocr(img: Image.Image) -> np.ndarray:
    """Light fallback preprocessing. Avoid expensive denoising unless raw OCR struggles."""
    img = resize_if_needed(img)
    bgr = pil_to_bgr(img)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def image_to_base64_jpeg(img: Image.Image, max_side: int = 1600, quality: int = 82) -> str:
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def is_blank(img: Image.Image, threshold: float = 3.0) -> bool:
    arr = np.array(img.convert("L").resize((200, 200)))
    return float(arr.std()) < threshold


def save_page_previews(pages: List[Image.Image], stem: str, cancel_check=None) -> List[Path]:
    s = get_settings()
    s.ensure_dirs()
    out = []
    for n, p in enumerate(pages, start=1):
        if cancel_check and cancel_check(): raise RuntimeError("__CANCELLED__")
        path = s.processed_dir / f"{stem}_p{n}.jpg"
        w, h = p.size
        if max(w, h) > 1600:
            k = 1600 / max(w, h)
            p = p.resize((int(w * k), int(h * k)), Image.LANCZOS)
        p.convert("RGB").save(path, "JPEG", quality=82, optimize=True)
        out.append(path)
    return out
