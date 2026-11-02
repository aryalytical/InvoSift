from pathlib import Path

from backend.config import Settings


def test_image_invoices_default_to_ocr_text_not_multimodal_vision(monkeypatch):
    monkeypatch.delenv("OLLAMA_USE_IMAGE", raising=False)
    settings = Settings()
    assert settings.ollama_use_image is False


def test_image_vision_can_still_be_enabled_explicitly(monkeypatch):
    monkeypatch.setenv("OLLAMA_USE_IMAGE", "true")
    settings = Settings()
    assert settings.ollama_use_image is True


def test_clear_details_cannot_replace_active_extraction_animation():
    source = (Path(__file__).parents[1] / "ui" / "app.js").read_text(encoding="utf-8")
    assert "if(extractionInProgress){toast('Wait until extraction finishes before clearing the details.');return;}" in source
    assert "classList.add('results-open','is-extracting')" in source
    assert "classList.remove('is-extracting')" in source


def test_results_panel_hides_header_actions_during_extraction():
    source = (Path(__file__).parents[1] / "ui" / "style.css").read_text(encoding="utf-8")
    assert ".results-panel.is-extracting .panel-heading" in source
    assert ".results-panel.is-extracting #resultActions" in source
