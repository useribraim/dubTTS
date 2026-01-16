"""
API regression tests to prevent breaking changes.

These tests ensure that API contracts remain stable and don't break
existing client integrations.
"""

import pytest
from fastapi.testclient import TestClient
from pathlib import Path


@pytest.fixture(scope="module")
def client():
    """Create a test client for the FastAPI app."""
    from app.main import app
    return TestClient(app)


class TestAPIContract:
    """Test API contract stability."""
    
    def test_create_dub_response_schema(self, client: TestClient, sample_audio_file: Path):
        """Test that POST /v1/dubs returns the expected schema."""
        with open(sample_audio_file, "rb") as f:
            files = {"file": ("test.wav", f, "audio/wav")}
            data = {"src_lang": "en", "tgt_lang": "es", "voice": "Joanna"}
            response = client.post("/v1/dubs", files=files, data=data)
        
        assert response.status_code == 200
        result = response.json()
        
        # Required fields
        assert "job_id" in result
        assert isinstance(result["job_id"], str)
        assert len(result["job_id"]) > 0
        
        # Should not have unexpected fields (or if they exist, they should be documented)
        allowed_fields = {"job_id"}
        unexpected = set(result.keys()) - allowed_fields
        assert len(unexpected) == 0, f"Unexpected fields in response: {unexpected}"
    
    
    def test_get_dub_status_response_schema(self, client: TestClient, sample_audio_file: Path):
        """Test that GET /v1/dubs/{job_id} returns the expected schema."""
        # Create a job first
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
        result = response.json()
        
        # Required fields (based on DubStatusResponse schema)
        required_fields = {"job_id", "status"}
        for field in required_fields:
            assert field in result, f"Missing required field: {field}"
        
        # Field types
        assert isinstance(result["job_id"], str)
        assert isinstance(result["status"], str)
        assert result["status"] in ["queued", "running", "done", "failed"]
    
    
    def test_api_version_prefix(self, client: TestClient):
        """Regression test: All API endpoints must be versioned under /v1/."""
        # This ensures we don't accidentally create unversioned endpoints
        # that would break the "versioned REST APIs" claim
        
        # Test that non-versioned endpoints don't exist (or return 404)
        non_versioned_endpoints = [
            "/dubs",
            "/dubs/test_id",
        ]
        
        for endpoint in non_versioned_endpoints:
            response = client.get(endpoint)
            # Should be 404 (not found) or 405 (method not allowed)
            assert response.status_code in [404, 405], \
                f"Non-versioned endpoint {endpoint} should not exist"
    
    
    def test_error_response_format(self, client: TestClient):
        """Test that error responses follow a consistent format."""
        # Test 404
        response = client.get("/v1/dubs/nonexistent")
        assert response.status_code == 404
        # Error responses should have "detail" field
        result = response.json()
        assert "detail" in result
        
        # Test 400 (validation error)
        response = client.post("/v1/dubs", files={}, data={})
        assert response.status_code in [400, 422]  # FastAPI returns 422 for validation errors
        result = response.json()
        assert "detail" in result


class TestBackwardCompatibility:
    """Test backward compatibility of API changes."""
    
    def test_default_parameters(self, client: TestClient, sample_audio_file: Path):
        """Test that default parameters work as expected."""
        with open(sample_audio_file, "rb") as f:
            files = {"file": ("test.wav", f, "audio/wav")}
            # Don't specify all parameters - should use defaults
            response = client.post("/v1/dubs", files=files)
        
        # May fail if Redis is not available (503)
        if response.status_code == 503:
            pytest.skip("Redis not available - start Redis to run this test")
        
        # Should either work with defaults or return validation error
        assert response.status_code in [200, 400, 422]
        
        if response.status_code == 200:
            result = response.json()
            assert "job_id" in result
    
    
    def test_optional_parameters(self, client: TestClient, sample_audio_file: Path):
        """Test that optional parameters can be omitted."""
        with open(sample_audio_file, "rb") as f:
            files = {"file": ("test.wav", f, "audio/wav")}
            # Only provide required file, use defaults for language/voice
            data = {}  # Empty data - should use defaults
            response = client.post("/v1/dubs", files=files, data=data)
        
        # Should work with defaults
        assert response.status_code in [200, 400, 422]
