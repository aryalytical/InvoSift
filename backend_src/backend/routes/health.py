from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path

from backend.config import get_settings
from backend.database import check_db
from backend.services import ai_extractor, ocr_service

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    """Status of every component. Never raises: a failing component is reported as false."""
    s = get_settings()
    ollama = ai_extractor.ollama_status()
    ocr = ocr_service.ocr_status()
    return {
        "backend": True,
        "ollama": ollama["reachable"],
        "model": ollama["model_installed"],
        "ocr": ocr["available"],
        "database": check_db(),
        "details": {
            "ollama_host": s.ollama_host,
            "model_name": s.ollama_model,
            "installed_models": ollama["models"],
            "ocr_engine": ocr["active"],
            "ocr_engines": ocr["engines"],
            "max_upload_mb": s.max_upload_mb,
        },
    }


@router.get("/ollama/models")
def ollama_models() -> dict:
    """Return the models installed in the local Ollama service."""
    status = ai_extractor.ollama_status()
    return {"connected": status["reachable"], "configured_model": get_settings().ollama_model, "models": status["models"]}


class ModelSelection(BaseModel):
    name: str

@router.post("/ollama/models/select")
def select_ollama_model(body: ModelSelection) -> dict:
    s = get_settings()
    status = ai_extractor.ollama_status()
    if not status["reachable"]:
        raise HTTPException(503, "Ollama is not connected.")
    if body.name not in status["models"]:
        raise HTTPException(404, "That model is not installed in Ollama.")
    s.data_dir.mkdir(parents=True, exist_ok=True)
    (s.data_dir / "selected_model.txt").write_text(body.name, encoding="utf-8")
    s.ollama_model = body.name
    return {"ok": True, "selected_model": body.name}
