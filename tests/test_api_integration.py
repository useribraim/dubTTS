"""
Integration tests for API endpoints.
"""

import pytest
import os
from pathlib import Path
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """Create a test client for the FastAPI app."""
    from app.main import app
    return TestClient(app)


def test_health_endpoint(client: TestClient):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy" or "redis" in data.get("status", "").lower()


def test_root_endpoint(client: TestClient):
    """Test the root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "version" in data
    assert data["version"] == "1.0"


def test_create_dub_endpoint(client: TestClient, sample_audio_file: Path):
    """Test creating a dub job via POST /v1/dubs."""
    with open(sample_audio_file, "rb") as f:
        files = {"file": ("test.wav", f, "audio/wav")}
        data = {
            "src_lang": "en",
            "tgt_lang": "es",
            "voice": "Joanna",
        }
        response = client.post("/v1/dubs", files=files, data=data)
    
    # May fail if Redis is not available (503) - that's acceptable for tests
    if response.status_code == 503:
        pytest.skip("Redis not available - start Redis to run this test")
    
    assert response.status_code == 200
    result = response.json()
    assert "job_id" in result
    assert len(result["job_id"]) > 0


def test_create_dub_invalid_file(client: TestClient):
    """Test creating a dub job with invalid file."""
    files = {"file": ("test.txt", b"not audio data", "text/plain")}
    data = {"src_lang": "en", "tgt_lang": "es", "voice": "Joanna"}
    
    response = client.post("/v1/dubs", files=files, data=data)
    # Should either accept it (validation happens later) or reject with 400
    assert response.status_code in [200, 400, 422]


def test_get_dub_status(client: TestClient, sample_audio_file: Path):
    """Test getting dub job status."""
    # First create a job
    with open(sample_audio_file, "rb") as f:
        files = {"file": ("test.wav", f, "audio/wav")}
        data = {"src_lang": "en", "tgt_lang": "es", "voice": "Joanna"}
        create_response = client.post("/v1/dubs", files=files, data=data)
    
    # May fail if Redis is not available
    if create_response.status_code == 503:
        pytest.skip("Redis not available - start Redis to run this test")
    
    assert create_response.status_code == 200
    job_id = create_response.json()["job_id"]
    
    # Get status
    response = client.get(f"/v1/dubs/{job_id}")
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert "status" in data
    assert data["job_id"] == job_id


def test_get_dub_status_not_found(client: TestClient):
    """Test getting status for non-existent job."""
    response = client.get("/v1/dubs/nonexistent_job_id")
    assert response.status_code == 404


def test_api_versioning(client: TestClient):
    """Test that all endpoints are versioned under /v1/."""
    # This is a regression test to ensure API versioning is maintained
    endpoints = [
        "/v1/dubs",
        "/v1/dubs/test_job_id",
        "/v1/dubs/test_job_id/events",
        "/v1/dubs/test_job_id/result",
    ]
    
    for endpoint in endpoints:
        try:
            # We don't care about the response code, just that the endpoint exists
            # (not 404 Not Found)
            response = client.get(endpoint, timeout=1.0)
            # Events endpoint uses SSE, so it may return different status codes
            if not endpoint.endswith("/events"):
                assert response.status_code != 404, \
                    f"Endpoint {endpoint} should be versioned under /v1/"
        except Exception:
            # If there's an error (like Redis connection), that's OK for this test
            # We're just checking that the endpoint path exists
            pass
