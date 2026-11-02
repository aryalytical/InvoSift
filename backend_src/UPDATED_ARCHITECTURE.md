# Updated architecture notes

This ZIP is an update of the existing working InvoSift project. It is not a rebuild.

## Problem 1: slow extraction

The old flow always rendered pages, ran PaddleOCR, then sent OCR text **and page images** to Gemma. For a digital PDF this repeated work that was unnecessary.

The new flow:

1. Validate upload.
2. Check local Ollama availability.
3. For PDFs, read the native PDF text layer first.
4. If the text layer is usable, skip PaddleOCR and use text-only Gemma extraction.
5. If there is no useful text layer, render pages and run PaddleOCR.
6. OCR tries the resized original image first and only uses light preprocessing as a fallback.
7. For scanned/image invoices, Gemma receives OCR text plus a limited number of page images.
8. Ollama uses `keep_alive` and bounded output generation.
9. Stage timings are stored in `processing_logs` and displayed/exported.

## Problem 2: shallow extraction

The schema now includes richer invoice metadata, supplier/customer registration data, complete financial fields, and a richer line-item model.

Line items are stored as separate SQLite rows and exported as separate Excel rows.

## Compatibility

The existing OCR fixes are preserved, including safe handling of PaddleOCR `rec_boxes` NumPy arrays and `enable_mkldnn=False`.

The database migration uses `ALTER TABLE` only for missing columns and never deletes existing invoice history.

## Tested locally in the build environment

- Python compilation of all backend files
- JavaScript syntax check
- Pydantic schema construction
- Native PDF text extraction with pypdfium2
- SQLite schema creation and migration from the previous schema
- Invoice persistence with new fields and line items
- JSON/CSV/Excel export generation
- End-to-end native-PDF pipeline using a fake local AI extractor

Actual PaddleOCR inference and Gemma inference depend on the user's installed Windows runtime and Ollama instance, so those two external runtime components cannot be fully exercised in this build environment.
