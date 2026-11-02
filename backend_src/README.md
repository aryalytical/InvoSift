# AI Invoice Data Extractor — local edition

A local invoice extraction application using **FastAPI + PaddleOCR + Ollama/Gemma 3:4b + SQLite + Excel**.
No invoice data is sent to a cloud AI API.

## What this version improves

### 1. Faster processing
- Digitally generated PDFs are checked for a usable native text layer first.
- If native PDF text is usable, **PaddleOCR is skipped entirely**.
- Image/scanned documents try the original image first; the heavier preprocessing pass is only a fallback.
- Gemma receives images only when they are useful (native-text PDFs use text-only extraction).
- Ollama keeps the model warm with `keep_alive` and uses a bounded output length.
- Every major stage records timing in SQLite and the invoice detail page.

### 2. Richer extraction
The structured schema now supports:
- Invoice number, dates, PO/reference number, currency, type and payment terms
- Supplier and customer name, address, email, phone, tax ID/GSTIN and registration number
- Every line item: item code, description, HSN/SAC, quantity, unit, unit price, discount, tax rate, tax amount and line total
- Subtotal, taxable amount, discounts, CGST, SGST, IGST, other charges, shipping, total tax, round-off, grand total, paid and balance due
- Deterministic arithmetic and consistency checks

## Architecture

```text
PDF / Image
    |
    +--> PDF native text available? ---- yes ---> Structured text
    |                                              |
    no                                             v
    |                                        Gemma 3:4b
    v                                              |
PaddleOCR ----------------------------------------+
    |                                               |
    +--> text + layout rows ------------------------+
                                                    v
                                           Pydantic structured data
                                                    |
                                              Validation checks
                                                    |
                                      SQLite + JSON + Excel export
```

For scanned/image invoices, PaddleOCR remains the primary OCR engine. Tesseract is an optional fallback.

## Requirements

- Windows / Linux / macOS
- Python 3.12 recommended for this project
- Ollama running locally
- `gemma3:4b` installed in Ollama

The important OCR versions are pinned in `requirements.txt`:

```text
paddleocr==3.3.1
paddlepaddle==3.2.2
numpy>=1.26,<2.4
```

## Windows setup

```bat
cd invoice
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
```

Make sure Ollama has the model:

```bat
ollama pull gemma3:4b
```

Start the application:

```bat
python run.py
```

Open:

```text
http://127.0.0.1:8000
```

## Speed tuning

The default configuration avoids unnecessary vision/OCR work. For scanned invoices, optional faster PaddleOCR models can be enabled in `.env` after their first download:

```text
PADDLE_DET_MODEL=PP-OCRv5_mobile_det
PADDLE_REC_MODEL=PP-OCRv5_mobile_rec
```

Leave these blank if you want to keep the default/cached PaddleOCR models.

## Excel output

A single invoice produces:

- **Invoice Summary**
- **Supplier**
- **Customer**
- **Line Items**
- **Financials**
- **Validation**
- **Processing**

The Line Items sheet keeps each invoice row as a separate row rather than flattening the whole table into one field.

## Existing data

The application does **not** delete the existing SQLite database. Startup performs a small schema migration with `ALTER TABLE` for the newly added fields. Existing invoice history is preserved.

## Important local-only rule

The backend communicates with Ollama only at the configured local `OLLAMA_HOST` (normally `http://localhost:11434`). No OpenAI, Anthropic, Google or other cloud AI API is required.
