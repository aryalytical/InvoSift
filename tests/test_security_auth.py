"""Regression tests for the per-launch local desktop API session."""
import os

from fastapi.testclient import TestClient

os.environ["INVOSIFT_SESSION_TOKEN"] = "security-test-token"

from backend.main import app


def test_api_rejects_missing_session_token():
    with TestClient(app) as client:
        response = client.get("/api/stats")
        assert response.status_code == 401


def test_api_rejects_invalid_session_token():
    with TestClient(app) as client:
        response = client.get("/api/stats", headers={"X-InvoSift-Session": "wrong-token"})
        assert response.status_code == 401


def test_api_accepts_valid_session_token():
    with TestClient(app) as client:
        response = client.get(
            "/api/stats",
            headers={"X-InvoSift-Session": "security-test-token"},
        )
        assert response.status_code == 200


def test_upload_endpoint_is_protected_before_multipart_processing():
    with TestClient(app) as client:
        response = client.post("/api/invoices/upload")
        assert response.status_code == 401
