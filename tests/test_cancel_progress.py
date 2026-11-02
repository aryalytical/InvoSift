import threading
import time

import pytest

from backend.services import ai_extractor


class BlockingStreamResponse:
    status_code = 200

    def __init__(self):
        self.closed = threading.Event()

    def iter_lines(self, **kwargs):
        # Simulate Ollama waiting indefinitely for its next token.
        self.closed.wait(5)
        if self.closed.is_set():
            raise RuntimeError("stream closed")
        yield b'{"message":{"content":"ok"},"done":true}'

    def close(self):
        self.closed.set()


def test_stop_closes_a_blocked_ollama_stream(monkeypatch):
    response = BlockingStreamResponse()
    cancelled = threading.Event()
    monkeypatch.setattr(ai_extractor.requests, "post", lambda *a, **k: response)
    result = {}

    def run():
        try:
            ai_extractor._chat([], "json", cancel_check=cancelled.is_set)
        except Exception as exc:  # the public processor maps this to its cancellation result
            result["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.12)
    cancelled.set()
    worker.join(timeout=1.0)

    assert not worker.is_alive(), "cancellation should interrupt the blocked stream"
    assert response.closed.is_set()
    assert isinstance(result.get("error"), ai_extractor.ExtractionCancelled)


def test_cancel_endpoint_signals_registered_extraction():
    from backend.routes import invoices as invoice_routes

    token = "test-cancel-token"
    event = threading.Event()
    invoice_routes._cancel_events[token] = event
    invoice_routes._progress.pop(token, None)
    try:
        result = invoice_routes.cancel_invoice_extraction(token)
        assert result["ok"] is True
        assert event.is_set()
        assert invoice_routes._progress[token]["stage"] == "cancelling"
    finally:
        invoice_routes._cancel_events.pop(token, None)
        invoice_routes._progress.pop(token, None)
