"""
Load tests with concurrency and p95 latency measurement.

These tests validate performance under load and measure p95 latency
as claimed in the resume.
"""

import asyncio
import time
import statistics
from typing import List, Dict
import pytest
import httpx
from pathlib import Path


class LoadTestResults:
    """Container for load test results."""
    
    def __init__(self):
        self.latencies: List[float] = []
        self.errors: List[str] = []
        self.success_count = 0
        self.total_count = 0
    
    def add_result(self, latency_ms: float, success: bool, error: str = None):
        """Add a test result."""
        self.total_count += 1
        if success:
            self.success_count += 1
            self.latencies.append(latency_ms)
        else:
            self.errors.append(error or "Unknown error")
    
    def get_p95_latency(self) -> float:
        """Calculate p95 latency in milliseconds."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        # p95: 95th percentile means 95% of values are below this
        # For n items, p95 is at index ceil(n * 0.95) - 1, or use min to ensure valid index
        p95_index = min(int(len(sorted_latencies) * 0.95), len(sorted_latencies) - 1)
        return sorted_latencies[p95_index]
    
    def get_p50_latency(self) -> float:
        """Calculate p50 (median) latency in milliseconds."""
        if not self.latencies:
            return 0.0
        return statistics.median(self.latencies)
    
    def get_p99_latency(self) -> float:
        """Calculate p99 latency in milliseconds."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        # p99: 99th percentile means 99% of values are below this
        p99_index = min(int(len(sorted_latencies) * 0.99), len(sorted_latencies) - 1)
        return sorted_latencies[p99_index]
    
    def get_stats(self) -> Dict:
        """Get comprehensive statistics."""
        if not self.latencies:
            return {
                "total": self.total_count,
                "success": self.success_count,
                "error_count": len(self.errors),
                "success_rate": 0.0,
            }
        
        return {
            "total": self.total_count,
            "success": self.success_count,
            "error_count": len(self.errors),
            "success_rate": self.success_count / self.total_count,
            "p50_latency_ms": self.get_p50_latency(),
            "p95_latency_ms": self.get_p95_latency(),
            "p99_latency_ms": self.get_p99_latency(),
            "min_latency_ms": min(self.latencies),
            "max_latency_ms": max(self.latencies),
            "mean_latency_ms": statistics.mean(self.latencies),
        }


async def make_request(
    client: httpx.AsyncClient,
    url: str,
    files: Dict,
    data: Dict,
    timeout: float = 30.0
):
    """Make a single HTTP request and measure latency."""
    start_time = time.time()
    try:
        response = await client.post(url, files=files, data=data, timeout=timeout)
        latency_ms = (time.time() - start_time) * 1000
        
        if response.status_code == 200:
            return True, latency_ms, None
        else:
            return False, latency_ms, f"HTTP {response.status_code}: {response.text[:100]}"
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        return False, latency_ms, str(e)


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running API server - use load_test_standalone.py for manual testing")
async def test_health_endpoint_concurrency():
    """Test health endpoint under concurrent load."""
    base_url = "http://127.0.0.1:8000"
    concurrency = 10
    requests_per_worker = 5
    
    # Check if server is available
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/health", timeout=2.0)
            if response.status_code != 200:
                pytest.skip("API server not responding correctly")
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip("API server not running - start with: uvicorn app.main:app --reload")
    
    results = LoadTestResults()
    
    async def worker():
        async with httpx.AsyncClient() as client:
            for _ in range(requests_per_worker):
                success, latency, error = await make_request(
                    client, f"{base_url}/health", files={}, data={}
                )
                results.add_result(latency, success, error)
    
    # Run concurrent workers
    workers = [worker() for _ in range(concurrency)]
    await asyncio.gather(*workers)
    
    stats = results.get_stats()
    
    # Assertions
    assert stats["success_rate"] > 0.95, f"Success rate too low: {stats['success_rate']}"
    assert stats["p95_latency_ms"] < 1000, f"p95 latency too high: {stats['p95_latency_ms']}ms"
    
    print(f"\nHealth Endpoint Load Test Results:")
    print(f"  Total requests: {stats['total']}")
    print(f"  Success rate: {stats['success_rate']:.2%}")
    print(f"  p50 latency: {stats['p50_latency_ms']:.2f}ms")
    print(f"  p95 latency: {stats['p95_latency_ms']:.2f}ms")
    print(f"  p99 latency: {stats['p99_latency_ms']:.2f}ms")


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running API server and sample audio file")
async def test_create_dub_endpoint_load(sample_audio_file: Path):
    """
    Load test for POST /v1/dubs endpoint.
    
    This test measures p95 latency under concurrent load.
    Note: Requires API server to be running and Redis available.
    """
    base_url = "http://127.0.0.1:8000"
    concurrency = 5  # Lower concurrency for file uploads
    requests_per_worker = 3
    
    results = LoadTestResults()
    
    # Read sample file once
    with open(sample_audio_file, "rb") as f:
        file_content = f.read()
    
    async def worker():
        async with httpx.AsyncClient() as client:
            for _ in range(requests_per_worker):
                files = {"file": ("test.wav", file_content, "audio/wav")}
                data = {"src_lang": "en", "tgt_lang": "es", "voice": "Joanna"}
                
                success, latency, error = await make_request(
                    client, f"{base_url}/v1/dubs", files=files, data=data, timeout=60.0
                )
                results.add_result(latency, success, error)
    
    # Run concurrent workers
    workers = [worker() for _ in range(concurrency)]
    await asyncio.gather(*workers)
    
    stats = results.get_stats()
    
    # Print results
    print(f"\nCreate Dub Endpoint Load Test Results:")
    print(f"  Total requests: {stats['total']}")
    print(f"  Success rate: {stats['success_rate']:.2%}")
    print(f"  p50 latency: {stats['p50_latency_ms']:.2f}ms")
    print(f"  p95 latency: {stats['p95_latency_ms']:.2f}ms")
    print(f"  p99 latency: {stats['p99_latency_ms']:.2f}ms")
    print(f"  Mean latency: {stats['mean_latency_ms']:.2f}ms")
    
    # Assertions (adjust thresholds based on your requirements)
    assert stats["success_rate"] > 0.80, f"Success rate too low: {stats['success_rate']}"
    # p95 latency for file upload + job creation should be reasonable
    # Adjust threshold based on your infrastructure
    assert stats["p95_latency_ms"] < 10000, f"p95 latency too high: {stats['p95_latency_ms']}ms"


def test_load_test_results_class():
    """Unit test for LoadTestResults class."""
    results = LoadTestResults()
    
    # Add some sample latencies
    latencies = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    for lat in latencies:
        results.add_result(lat, True)
    
    # Median of [10,20,30,40,50,60,70,80,90,100] is 55 (average of 50 and 60)
    assert results.get_p50_latency() == 55.0
    # p95: 10 items * 0.95 = 9.5, int(9.5) = 9, so index 9 = 100
    # For 10 items, p95 is at index 9 (the 10th item) = 100
    assert results.get_p95_latency() == 100.0
    # p99: 10 items * 0.99 = 9.9, int(9.9) = 9, so index 9 = 100
    assert results.get_p99_latency() == 100.0
    
    stats = results.get_stats()
    assert stats["total"] == 10
    assert stats["success"] == 10
    assert stats["success_rate"] == 1.0
    assert "p95_latency_ms" in stats
