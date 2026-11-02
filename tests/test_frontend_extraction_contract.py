from pathlib import Path
import re


def test_extract_handler_uses_collection_selector_for_all_steps():
    source = (Path(__file__).parents[1] / "ui" / "app.js").read_text(encoding="utf-8")
    # $ is querySelector (single node); $$ is querySelectorAll (collection).
    assert not re.search(r"(?<!\$)\$\('#extractSteps \[data-step\]'\)\.forEach", source)
    assert "$$('#extractSteps [data-step]').forEach" in source


def test_progress_polling_starts_before_upload_response():
    source = (Path(__file__).parents[1] / "ui" / "app.js").read_text(encoding="utf-8")
    start = source.index("const progressOutcome=waitForExtraction(token)")
    upload = source.index("await json('/api/invoices/upload'", start)
    assert start < upload
    assert "activeExtractionToken=null;" in source[start:source.index("throw uploadError", start)]


def test_processor_does_not_publish_complete_before_invoice_id_is_attached():
    processor = (Path(__file__).parents[1] / "backend_src" / "backend" / "services" / "invoice_processor.py").read_text(encoding="utf-8")
    assert 'progress("complete", 100' not in processor
    assert 'progress("save", 99, "Finalising the saved invoice record")' in processor
