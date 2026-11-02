"""Upload validation and safe file handling."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Tuple

from backend.config import get_settings

# Magic-byte signatures: we trust the content, not the extension or client-sent MIME type.
_SIGNATURES = {
    ".pdf": [b"%PDF-"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
}
_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class FileValidationError(Exception):
    """Raised with a user-friendly message when an upload is not acceptable."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def sanitize_filename(name: str) -> str:
    """Strip any path components and unsafe characters. Never returns an empty string."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    name = re.sub(r"_{2,}", "_", name)
    return name[:120] or "invoice"


def validate_upload(filename: str, content: bytes) -> Tuple[str, str]:
    """Validate an upload. Returns (extension, mime_type) or raises FileValidationError."""
    s = get_settings()
    if not content:
        raise FileValidationError("The uploaded file is empty.")
    if len(content) > s.max_upload_bytes:
        raise FileValidationError(
            f"The file is too large. The maximum allowed size is {s.max_upload_mb} MB.", status_code=413
        )
    ext = Path(sanitize_filename(filename)).suffix.lower()
    if ext not in s.allowed_extensions:
        raise FileValidationError("Unsupported file type. Please upload a PDF, PNG, JPG or JPEG file.", 415)
    if not any(content.startswith(sig) for sig in _SIGNATURES[ext]):
        raise FileValidationError(
            f"This file does not look like a valid {ext.lstrip('.').upper()} file. It may be corrupted or renamed.",
            415,
        )
    return ext, _MIME[ext]


def save_upload(content: bytes, ext: str) -> Path:
    """Store the upload under a random name inside uploads/. User-supplied names never touch the filesystem."""
    s = get_settings()
    s.ensure_dirs()
    path = s.uploads_dir / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(content)
    return path


def resolve_stored_file(stored_filename: str) -> Path:
    """Resolve a stored filename, refusing anything that escapes the uploads directory."""
    s = get_settings()
    base = s.uploads_dir.resolve()
    path = (base / Path(stored_filename).name).resolve()
    if base not in path.parents:
        raise FileValidationError("Invalid file path.", 400)
    return path


def delete_stored_files(stored_filename: str) -> None:
    s = get_settings()
    try:
        path = resolve_stored_file(stored_filename)
        path.unlink(missing_ok=True)
        for p in s.processed_dir.glob(f"{path.stem}*"):
            p.unlink(missing_ok=True)
    except Exception:
        pass
