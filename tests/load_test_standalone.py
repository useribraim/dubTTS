#!/usr/bin/env python3
"""
Standalone load test script for measuring p95 latency.

This script can be run independently to test the API under load.
It measures concurrency, p95 latency, and generates a report.

Usage:
    python tests/load_test_standalone.py [--concurrency N] [--requests N] [--base-url URL]
"""

import asyncio
import argparse
import time
import statistics
from typing import List, Dict
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
        p95_index = int(len(sorted_latencies) * 0.95)
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
        p99_index = int(len(sorted_latencies) * 0.99)
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
    files: Dict = None,
    data: Dict = None,
    timeout: float = 30.0
):
    """Make a single HTTP request and measure latency."""
    start_time = time.time()
    try:
        if files:
            response = await client.post(url, files=files, data=data, timeout=timeout)
        else:
            response = await client.get(url, timeout=timeout)
        latency_ms = (time.time() - start_time) * 1000
        
        if response.status_code == 200:
            return True, latency_ms, None
        else:
            return False, latency_ms, f"HTTP {response.status_code}: {response.text[:100]}"
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        return False, latency_ms, str(e)


async def load_test_health(base_url: str, concurrency: int, requests_per_worker: int):
    """Load test the health endpoint."""
    results = LoadTestResults()
    
    async def worker():
        async with httpx.AsyncClient() as client:
            for _ in range(requests_per_worker):
                success, latency, error = await make_request(
                    client, f"{base_url}/health"
                )
                results.add_result(latency, success, error)
    
    print(f"Starting load test: {concurrency} concurrent workers, {requests_per_worker} requests each...")
    start_time = time.time()
    
    workers = [worker() for _ in range(concurrency)]
    await asyncio.gather(*workers)
    
    total_time = time.time() - start_time
    
    stats = results.get_stats()
    
    print("\n" + "=" * 60)
    print("LOAD TEST RESULTS - Health Endpoint")
    print("=" * 60)
    print(f"Configuration:")
    print(f"  Concurrency: {concurrency}")
    print(f"  Requests per worker: {requests_per_worker}")
    print(f"  Total requests: {stats['total']}")
    print(f"  Total time: {total_time:.2f}s")
    print(f"\nResults:")
    print(f"  Success rate: {stats['success_rate']:.2%}")
    print(f"  Errors: {stats['error_count']}")
    print(f"\nLatency (ms):")
    print(f"  Min: {stats.get('min_latency_ms', 0):.2f}")
    print(f"  p50 (median): {stats.get('p50_latency_ms', 0):.2f}")
    print(f"  p95: {stats.get('p95_latency_ms', 0):.2f}")
    print(f"  p99: {stats.get('p99_latency_ms', 0):.2f}")
    print(f"  Max: {stats.get('max_latency_ms', 0):.2f}")
    print(f"  Mean: {stats.get('mean_latency_ms', 0):.2f}")
    print("=" * 60)
    
    return stats


async def load_test_create_dub(base_url: str, concurrency: int, requests_per_worker: int, audio_file: Path):
    """Load test the create dub endpoint."""
    results = LoadTestResults()
    
    # Read sample file once
    with open(audio_file, "rb") as f:
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
    
    print(f"Starting load test: {concurrency} concurrent workers, {requests_per_worker} requests each...")
    start_time = time.time()
    
    workers = [worker() for _ in range(concurrency)]
    await asyncio.gather(*workers)
    
    total_time = time.time() - start_time
    
    stats = results.get_stats()
    
    print("\n" + "=" * 60)
    print("LOAD TEST RESULTS - Create Dub Endpoint")
    print("=" * 60)
    print(f"Configuration:")
    print(f"  Concurrency: {concurrency}")
    print(f"  Requests per worker: {requests_per_worker}")
    print(f"  Total requests: {stats['total']}")
    print(f"  Total time: {total_time:.2f}s")
    print(f"\nResults:")
    print(f"  Success rate: {stats['success_rate']:.2%}")
    print(f"  Errors: {stats['error_count']}")
    print(f"\nLatency (ms):")
    print(f"  Min: {stats.get('min_latency_ms', 0):.2f}")
    print(f"  p50 (median): {stats.get('p50_latency_ms', 0):.2f}")
    print(f"  p95: {stats.get('p95_latency_ms', 0):.2f}")
    print(f"  p99: {stats.get('p99_latency_ms', 0):.2f}")
    print(f"  Max: {stats.get('max_latency_ms', 0):.2f}")
    print(f"  Mean: {stats.get('mean_latency_ms', 0):.2f}")
    print("=" * 60)
    
    return stats


def main():
    parser = argparse.ArgumentParser(description="Load test the Dub MVP API")
    parser.add_argument("--concurrency", type=int, default=10, help="Number of concurrent workers")
    parser.add_argument("--requests", type=int, default=5, help="Requests per worker")
    parser.add_argument("--base-url", type=str, default="http://127.0.0.1:8000", help="Base URL of the API")
    parser.add_argument("--endpoint", type=str, choices=["health", "create_dub", "both"], default="health",
                       help="Which endpoint to test")
    parser.add_argument("--audio-file", type=Path, help="Audio file for create_dub test")
    
    args = parser.parse_args()
    
    if args.endpoint == "create_dub" and not args.audio_file:
        print("Error: --audio-file is required for create_dub endpoint test")
        return 1
    
    if args.endpoint in ["health", "both"]:
        asyncio.run(load_test_health(args.base_url, args.concurrency, args.requests))
    
    if args.endpoint in ["create_dub", "both"]:
        if not args.audio_file or not args.audio_file.exists():
            print(f"Error: Audio file not found: {args.audio_file}")
            return 1
        asyncio.run(load_test_create_dub(args.base_url, args.concurrency, args.requests, args.audio_file))
    
    return 0


if __name__ == "__main__":
    exit(main())
