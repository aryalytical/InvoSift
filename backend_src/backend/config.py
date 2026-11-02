"""Application configuration."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else BASE_DIR / p


class Settings:
    def __init__(self) -> None:
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "gemma3:4b")
        self.ollama_timeout = _int("OLLAMA_TIMEOUT", 600)
        # OCR text is the default source for image invoices. Sending full image tensors to
        # small local vision models can stall or take several minutes on CPU/shared-memory
        # systems. Vision can still be explicitly enabled for installations that need it.
        self.ollama_use_image = os.getenv("OLLAMA_USE_IMAGE", "false").lower() in ("1", "true", "yes")
        self.ollama_max_images = _int("OLLAMA_MAX_IMAGES", 3)
        self.ollama_temperature = _float("OLLAMA_TEMPERATURE", 0.0)
        self.ollama_num_ctx = _int("OLLAMA_NUM_CTX", 8192)
        self.ollama_num_predict = _int("OLLAMA_NUM_PREDICT", 3072)
        self.ollama_keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "10m")

        self.ocr_engine = os.getenv("OCR_ENGINE", "auto").lower()
        self.ocr_lang = os.getenv("OCR_LANG", "en")
        self.tesseract_cmd = os.getenv("TESSERACT_CMD", "")
        self.ocr_low_confidence = _float("OCR_LOW_CONFIDENCE", 0.70)
        self.pdf_render_scale = _float("PDF_RENDER_SCALE", 1.75)
        self.max_pdf_pages = _int("MAX_PDF_PAGES", 20)
        self.max_image_side = _int("MAX_IMAGE_SIDE", 2400)
        self.ocr_preprocess_fallback = os.getenv("OCR_PREPROCESS_FALLBACK", "true").lower() in ("1", "true", "yes")
        self.paddle_det_model = os.getenv("PADDLE_DET_MODEL", "").strip()
        self.paddle_rec_model = os.getenv("PADDLE_REC_MODEL", "").strip()

        self.data_dir = _path("DATA_DIR", "data")
        try:
            saved_model = (self.data_dir / "selected_model.txt").read_text(encoding="utf-8").strip()
            if saved_model:
                self.ollama_model = saved_model
        except OSError:
            pass
        self.uploads_dir = self.data_dir / "uploads"
        self.processed_dir = self.data_dir / "processed"
        self.exports_dir = self.data_dir / "exports"
        self.database_path = _path("DATABASE_PATH", "data/invoices.db")

        self.max_upload_mb = _int("MAX_UPLOAD_MB", 20)
        self.allowed_extensions = {".pdf", ".png", ".jpg", ".jpeg"}
        self.amount_tolerance = _float("AMOUNT_TOLERANCE", 1.0)

        # The desktop backend must never bind to a public/network interface.
        self.host = "127.0.0.1"
        self.port = _int("PORT", 8000)
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.processed_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    global _settings
    if _settings is None or refresh:
        _settings = Settings()
    return _settings
