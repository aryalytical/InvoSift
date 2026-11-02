"""Local structured extraction through Ollama/Gemma. No cloud APIs are used."""
from __future__ import annotations

import json
import logging
import re
import socket
import threading
from typing import Any, Dict, List, Optional, Tuple

import requests

from backend.config import get_settings
from backend.schemas.invoice_schema import InvoiceData

logger = logging.getLogger("invoice_ai.ai")


class AIExtractionError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ExtractionCancelled(Exception):
    """Raised when the user stops an in-flight local extraction."""


class OllamaUnavailable(AIExtractionError):
    def __init__(self) -> None:
        s = get_settings()
        super().__init__(f"Ollama is not running at {s.ollama_host}. Start Ollama and make sure '{s.ollama_model}' is installed.", 503)


class ModelNotInstalled(AIExtractionError):
    def __init__(self, model: str) -> None:
        super().__init__(f"The Ollama model '{model}' is not installed. Run: ollama pull {model}", 503)


class InvalidAIResponse(AIExtractionError):
    pass


def _model_matches(installed: str, wanted: str) -> bool:
    installed, wanted = installed.lower(), wanted.lower()
    return installed == wanted or (":" not in wanted and installed == f"{wanted}:latest")


def ollama_status() -> Dict[str, Any]:
    s = get_settings()
    try:
        r = requests.get(f"{s.ollama_host}/api/tags", timeout=4)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        return {"reachable": True, "model_installed": any(_model_matches(n, s.ollama_model) for n in names), "models": names}
    except Exception:
        return {"reachable": False, "model_installed": False, "models": []}


def ensure_ready() -> None:
    st = ollama_status()
    if not st["reachable"]:
        raise OllamaUnavailable()
    if not st["model_installed"]:
        raise ModelNotInstalled(get_settings().ollama_model)


SCHEMA_EXAMPLE = {
    "invoice_number": None,
    "invoice_date": None,
    "due_date": None,
    "purchase_order_number": None,
    "reference_number": None,
    "currency": None,
    "invoice_type": None,
    "payment_terms": None,
    "supplier": {
        "name": None, "address": None, "email": None, "phone": None,
        "tax_id": None, "registration_number": None,
    },
    "customer": {
        "name": None, "address": None, "email": None, "phone": None,
        "tax_id": None, "registration_number": None,
    },
    "items": [{
        "item_code": None, "description": None, "hsn_sac": None,
        "quantity": None, "unit": None, "unit_price": None,
        "discount": None, "tax_rate": None, "tax": None, "total": None,
    }],
    "financials": {
        "subtotal": None, "taxable_amount": None, "discount": None,
        "cgst": None, "sgst": None, "igst": None, "other_charges": None,
        "shipping": None, "tax": None, "round_off": None,
        "grand_total": None, "amount_paid": None, "balance_due": None,
    },
}

SYSTEM_PROMPT = """You are a high-precision invoice extraction engine. Return exactly ONE JSON object.

SOURCE PRIORITY:
1. Attached invoice image(s), when present.
2. OCR/native PDF text as a transcription aid.
Never invent a value that is not supported by the source.

EXTRACTION REQUIREMENTS:
- Extract invoice metadata, supplier, customer, financial totals and EVERY line item.
- Preserve all line-item rows. If the table has 12 products/services, output 12 items.
- Do not turn subtotal/tax/grand-total rows into line items.
- Use the row/column layout to keep description, quantity, price, tax and total paired correctly.
- Extract HSN/SAC, SKU/item code and tax rate when visible.
- Extract GSTIN/tax IDs, payment terms and reference/PO numbers when visible.
- If a field is absent or unreadable, return null. Never guess and never use 0 for missing data.
- Keep numeric values as JSON numbers without currency symbols or thousands separators.
- 'tax' in financials is the total tax amount; CGST/SGST/IGST are amounts, not rates.
- Per-item 'tax' is ONLY the actual tax amount for that row, never the line total or unit price. 'tax_rate' is the percentage when visible.
- Distinguish pre-tax line amount, tax amount, and gross line total. If a table has columns line_total and tax_amount, map them independently.
- If quantity × unit_price equals line_total, do not copy line_total into tax.
- Preserve the order and exact descriptions of all item rows. Treat CGST/SGST/IGST summary rows as tax breakdowns, not products.
- Currency should be an ISO-style code when clear (₹/Rs -> INR, $ -> USD, € -> EUR, £ -> GBP).
- Use YYYY-MM-DD only when the date is unambiguous; otherwise preserve the printed date.
- Return null rather than hallucinating.

OUTPUT RULES:
- JSON only. No markdown. No explanation.
- All schema keys must be present.

SCHEMA EXAMPLE:
""" + json.dumps(SCHEMA_EXAMPLE, indent=2)


def _user_prompt(ocr_text: str, with_images: bool) -> str:
    intro = (
        "The invoice image(s) are attached. Use them as the source of truth; the text below is OCR/native PDF text and may contain errors."
        if with_images else
        "Use the supplied native/OCR text as the source of truth. Preserve table row structure from the spacing/order where possible."
    )
    text = ocr_text.strip()
    # Keep the prompt inside Gemma's context while allowing normal multi-line invoices.
    if len(text) > 28000:
        text = text[:28000] + "\n[TEXT TRUNCATED — prioritize invoice header, all table rows and totals above this point]"
    return f"{intro}\n\n--- DOCUMENT TEXT START ---\n{text}\n--- DOCUMENT TEXT END ---\n\nReturn the JSON object now."


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text or not text.strip():
        raise InvalidAIResponse("The AI model returned an empty response.")
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    break
        start = t.find("{", start + 1)
    raise InvalidAIResponse("The AI model did not return valid JSON.")


def _chat(messages: List[Dict[str, Any]], fmt: Any, cancel_check=None, progress_callback=None) -> str:
    s = get_settings()
    streaming = cancel_check is not None
    payload: Dict[str, Any] = {
        "model": s.ollama_model,
        "messages": messages,
        "stream": streaming,
        "keep_alive": s.ollama_keep_alive,
        "options": {
            "temperature": s.ollama_temperature,
            "num_ctx": s.ollama_num_ctx,
            "num_predict": s.ollama_num_predict,
        },
    }
    if fmt is not None:
        payload["format"] = fmt
    try:
        r = requests.post(f"{s.ollama_host}/api/chat", json=payload, timeout=s.ollama_timeout, stream=streaming)
    except requests.ConnectionError as exc:
        raise OllamaUnavailable() from exc
    except requests.Timeout as exc:
        raise AIExtractionError("The local AI model took too long to respond. Try a smaller invoice or increase OLLAMA_TIMEOUT.", 504) from exc
    if r.status_code == 404 and "not found" in r.text.lower():
        raise ModelNotInstalled(s.ollama_model)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", "")
        except Exception:
            err = r.text[:300]
        e = AIExtractionError(f"Ollama returned an error: {err or r.status_code}")
        e.ollama_error = err  # type: ignore[attr-defined]
        e.http_status = r.status_code  # type: ignore[attr-defined]
        raise e
    cancel_monitor_stop = threading.Event()
    cancel_monitor = None
    if streaming:
        # Closing the live HTTP response from a monitor thread interrupts a blocked
        # streaming read instead of waiting indefinitely for the model's next token.
        def watch_cancel():
            while not cancel_monitor_stop.wait(0.05):
                if cancel_check and cancel_check():
                    try:
                        raw = getattr(r, "raw", None)
                        sock = getattr(getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None), "_sock", None)
                        if sock is not None:
                            try:
                                sock.shutdown(socket.SHUT_RDWR)
                            except Exception:
                                pass
                            sock.close()
                    except Exception:
                        pass
                    try:
                        r.close()
                    except Exception:
                        pass
                    return
        cancel_monitor = threading.Thread(target=watch_cancel, name="invosift-cancel-watch", daemon=True)
        cancel_monitor.start()
    try:
        if streaming:
            chunks = []
            received_chars = 0
            for line in r.iter_lines(chunk_size=1, decode_unicode=True):
                if cancel_check and cancel_check():
                    raise ExtractionCancelled()
                if not line:
                    continue
                packet = json.loads(line)
                content = (packet.get("message") or {}).get("content")
                if content:
                    chunks.append(content)
                    received_chars += len(content)
                    if progress_callback:
                        progress_callback(received_chars)
                if packet.get("done"):
                    break
            if cancel_check and cancel_check():
                raise ExtractionCancelled()
            return "".join(chunks)
        return r.json()["message"]["content"]
    except ExtractionCancelled:
        raise
    except Exception as exc:
        if cancel_check and cancel_check():
            raise ExtractionCancelled() from exc
        raise InvalidAIResponse("Ollama returned an unexpected response format.") from exc
    finally:
        cancel_monitor_stop.set()
        r.close()
        if cancel_monitor and cancel_monitor.is_alive():
            cancel_monitor.join(timeout=0.15)


def _chat_with_format_fallback(messages: List[Dict[str, Any]], cancel_check=None, progress_callback=None) -> str:
    try:
        return _chat(messages, InvoiceData.model_json_schema(), cancel_check, progress_callback)
    except AIExtractionError as exc:
        if getattr(exc, "http_status", None) == 400 and "image" not in getattr(exc, "ollama_error", "").lower():
            logger.info("Ollama rejected JSON schema; retrying with JSON mode")
            return _chat(messages, "json", cancel_check, progress_callback)
        raise


def extract_invoice(
    ocr_text: str,
    page_images_b64: Optional[List[str]] = None,
    use_images: Optional[bool] = None,
    cancel_check=None,
    progress_callback=None,
) -> Tuple[InvoiceData, str]:
    s = get_settings()
    if use_images is None:
        use_images = s.ollama_use_image
    images = (page_images_b64 or [])[: s.ollama_max_images] if use_images else []
    with_images = bool(images)

    user_msg: Dict[str, Any] = {"role": "user", "content": _user_prompt(ocr_text, with_images)}
    if with_images:
        user_msg["images"] = images
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, user_msg]

    try:
        content = _chat_with_format_fallback(messages, cancel_check, progress_callback)
    except AIExtractionError as exc:
        if with_images and "image" in getattr(exc, "ollama_error", "").lower():
            logger.warning("Model rejected images; falling back to text-only extraction")
            content = _chat_with_format_fallback([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(ocr_text, False)},
            ], cancel_check, progress_callback)
        else:
            raise

    try:
        obj = _extract_json_object(content)
        data = InvoiceData.model_validate(obj)
        if not data.items:
            logger.warning("Gemma returned no line items")
        return data, json.dumps(obj, ensure_ascii=False)
    except (InvalidAIResponse, ValueError) as exc:
        logger.info("Invalid structured AI response; performing one repair request")
        repair = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(ocr_text, False)},
            {"role": "assistant", "content": content or ""},
            {"role": "user", "content": "Repair the previous response. Return ONLY valid JSON matching the schema. Preserve every line item and do not invent missing values."},
        ]
        repaired = _chat(repair, "json", cancel_check, progress_callback)
        try:
            obj = _extract_json_object(repaired)
            data = InvoiceData.model_validate(obj)
            return data, json.dumps(obj, ensure_ascii=False)
        except Exception as repair_exc:
            raise InvalidAIResponse("The local AI model returned invoice data that could not be validated as structured JSON.") from repair_exc
