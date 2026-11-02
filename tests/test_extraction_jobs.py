import time
from types import SimpleNamespace
from threading import Event

from starlette.datastructures import UploadFile
from io import BytesIO

from backend.routes import invoices as routes


def _wait_terminal(token, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        state = routes.invoice_progress(token)
        if state.get('stage') in {'complete', 'cancelled', 'error'}:
            return state
        time.sleep(0.02)
    raise AssertionError(f'extraction job did not finish: {routes.invoice_progress(token)}')


def test_upload_queues_job_and_reports_real_completion(monkeypatch):
    token = 'test-queued-job'

    def fake_process(filename, content, progress_callback=None, cancel_check=None):
        assert filename == 'sample.pdf'
        assert content == b'%PDF-test'
        progress_callback({'stage': 'ocr', 'percent': 47, 'detail': 'Recognised page 1 of 1'})
        return SimpleNamespace(id=987)

    monkeypatch.setattr(routes, 'process_upload', fake_process)
    routes._progress.pop(token, None)
    routes._cancel_events.pop(token, None)
    upload = UploadFile(filename='sample.pdf', file=BytesIO(b'%PDF-test'))
    ack = routes.upload_invoice(upload, token)
    assert ack == {'token': token, 'stage': 'queued'}
    final = _wait_terminal(token)
    assert final['stage'] == 'complete'
    assert final['percent'] == 100
    assert final['invoice_id'] == 987


def test_cancelled_job_does_not_report_success(monkeypatch):
    token = 'test-cancel-job'
    started = Event()

    def fake_process(filename, content, progress_callback=None, cancel_check=None):
        started.set()
        while not cancel_check():
            time.sleep(0.01)
        from backend.services.invoice_processor import ProcessingCancelled
        raise ProcessingCancelled('stopped')

    monkeypatch.setattr(routes, 'process_upload', fake_process)
    routes._progress.pop(token, None)
    routes._cancel_events.pop(token, None)
    upload = UploadFile(filename='sample.pdf', file=BytesIO(b'%PDF-test'))
    routes.upload_invoice(upload, token)
    assert started.wait(1)
    assert routes.cancel_invoice_extraction(token)['ok'] is True
    final = _wait_terminal(token)
    assert final['stage'] == 'cancelled'
    assert 'invoice_id' not in final


def test_cancel_can_arrive_before_multipart_upload_finishes(monkeypatch):
    token = 'test-pre-cancel-upload'
    routes._progress.pop(token, None)
    routes._cancel_events.pop(token, None)
    ack = routes.cancel_invoice_extraction(token)
    assert ack['ok'] is True
    upload = UploadFile(filename='sample.pdf', file=BytesIO(b'%PDF-test'))
    result = routes.upload_invoice(upload, token)
    assert result['stage'] == 'cancelled'
    assert routes.invoice_progress(token)['stage'] == 'cancelled'


def test_rejected_upload_sets_terminal_error_state():
    token = 'test-rejected-upload'
    routes._progress.pop(token, None)
    routes._cancel_events.pop(token, None)
    upload = UploadFile(filename='empty.pdf', file=BytesIO(b''))
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        routes.upload_invoice(upload, token)
    assert exc.value.status_code == 422
    state = routes.invoice_progress(token)
    assert state['stage'] == 'error'
    assert 'empty' in state['detail'].lower()


def test_worker_publishes_complete_only_with_invoice_id(monkeypatch):
    token = 'test-complete-envelope'

    def fake_process(filename, content, progress_callback=None, cancel_check=None):
        progress_callback({'stage': 'save', 'percent': 99, 'detail': 'Finalising'})
        return SimpleNamespace(id=654)

    monkeypatch.setattr(routes, 'process_upload', fake_process)
    routes._progress.pop(token, None)
    routes._cancel_events.pop(token, None)
    upload = UploadFile(filename='sample.pdf', file=BytesIO(b'%PDF-test'))
    routes.upload_invoice(upload, token)
    final = _wait_terminal(token)
    assert final['stage'] == 'complete'
    assert final['invoice_id'] == 654
