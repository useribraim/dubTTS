#!/usr/bin/env python3
"""
Performance Report Generator

Generates a human-readable performance report showing:
- p50, p95, p99 latency metrics
- Cache hit/miss rates
- Performance improvements from caching

Usage:
    python performance_report.py
"""

import asyncio
import json
import sys
from datetime import datetime

# Add app directory to path
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.metrics import get_performance_report


def format_latency_ms(ms: float) -> str:
    """Format latency in milliseconds."""
    if ms < 1:
        return f"{ms:.2f}ms"
    elif ms < 1000:
        return f"{ms:.1f}ms"
    else:
        return f"{ms/1000:.2f}s"


def print_metrics_report(report: dict):
    """Print a formatted performance report."""
    print("=" * 80)
    print("PERFORMANCE METRICS REPORT")
    print("=" * 80)
    print(f"Generated: {report.get('timestamp', 'N/A')}")
    print()
    
    # Latency Metrics
    latency_metrics = report.get("latency_metrics", {})
    if latency_metrics:
        print("LATENCY METRICS (p50, p95, p99)")
        print("-" * 80)
        
        for operation, metrics in sorted(latency_metrics.items()):
            print(f"\n{operation.upper()}:")
            print(f"  Count:        {metrics.get('count', 0)} samples")
            print(f"  p50 (median):  {format_latency_ms(metrics.get('p50', 0))}")
            print(f"  p95:           {format_latency_ms(metrics.get('p95', 0))}")
            print(f"  p99:           {format_latency_ms(metrics.get('p99', 0))}")
            print(f"  Min:           {format_latency_ms(metrics.get('min', 0))}")
            print(f"  Max:           {format_latency_ms(metrics.get('max', 0))}")
            print(f"  Mean:          {format_latency_ms(metrics.get('mean', 0))}")
    else:
        print("LATENCY METRICS: No data available yet")
        print("  (Process some jobs to collect metrics)")
    
    print()
    print("-" * 80)
    
    # Cache Statistics
    cache_stats = report.get("cache_stats", {})
    if cache_stats:
        print("\nCACHE STATISTICS")
        print("-" * 80)
        
        for operation, stats in sorted(cache_stats.items()):
            print(f"\n{operation.upper()}:")
            print(f"  Total requests:  {stats.get('total', 0)}")
            print(f"  Cache hits:      {stats.get('hits', 0)} ({stats.get('hit_rate', 0)*100:.1f}%)")
            print(f"  Cache misses:     {stats.get('misses', 0)} ({stats.get('miss_rate', 0)*100:.1f}%)")
    else:
        print("\nCACHE STATISTICS: No data available yet")
    
    print()
    print("-" * 80)
    
    # Cache Improvement (Before vs After)
    cache_improvement = report.get("cache_improvement", {})
    if cache_improvement:
        print("\n" + "=" * 80)
        print("CACHING IMPROVEMENT (Before vs After)")
        print("=" * 80)
        print("\nTranslation Caching Performance:")
        print(f"  Cache hit rate:              {cache_improvement.get('hit_rate', 0)*100:.1f}%")
        print()
        print("  ACTUAL MEASURED LATENCIES:")
        hit_latency = cache_improvement.get('cache_hit_latency', {})
        miss_latency = cache_improvement.get('cache_miss_latency', {})
        
        if hit_latency:
            print(f"    Cache HIT (p50):          {format_latency_ms(hit_latency.get('p50', 0))}")
            print(f"    Cache HIT (p95):          {format_latency_ms(hit_latency.get('p95', 0))}")
        if miss_latency:
            print(f"    Cache MISS (p50):         {format_latency_ms(miss_latency.get('p50', 0))}")
            print(f"    Cache MISS (p95):         {format_latency_ms(miss_latency.get('p95', 0))}")
        
        print()
        print("  WEIGHTED AVERAGE (with cache):")
        print(f"    {format_latency_ms(cache_improvement.get('avg_latency_with_cache_ms', 0))}")
        print("  WITHOUT CACHE (baseline):")
        print(f"    {format_latency_ms(cache_improvement.get('avg_latency_without_cache_ms', 0))}")
        print()
        print("  IMPROVEMENT:")
        print(f"    {cache_improvement.get('improvement_factor', 1.0):.2f}x faster")
        print(f"    {cache_improvement.get('improvement_percent', 0):.1f}% reduction in latency")
        print()
        print("  INTERVIEW TALKING POINTS:")
        print(f"    • Cache hits are {cache_improvement.get('cache_hit_p50_ms', 0):.0f}ms vs {cache_improvement.get('cache_miss_p50_ms', 0):.0f}ms for API calls")
        print(f"    • With {cache_improvement.get('hit_rate', 0)*100:.0f}% hit rate, average latency improved by {cache_improvement.get('improvement_percent', 0):.0f}%")
        print(f"    • p95 latency reduced from {format_latency_ms(cache_improvement.get('avg_latency_without_cache_ms', 0))} to {format_latency_ms(cache_improvement.get('avg_latency_with_cache_ms', 0))}")
    else:
        print("\nCACHING IMPROVEMENT: No cache data available yet")
        print("  (Process some jobs with repeated translations to see improvement)")
    
    # Incremental Output Improvement
    incremental_improvement = report.get("incremental_improvement", {})
    if incremental_improvement:
        print("\n" + "=" * 80)
        print("INCREMENTAL OUTPUT IMPROVEMENT (Streaming vs Batch)")
        print("=" * 80)
        print("\nTime-to-First-Segment (Streaming):")
        print(f"  p95: {format_latency_ms(incremental_improvement.get('time_to_first_segment_p95_ms', 0))}")
        print("\nEnd-to-End (Full Processing):")
        print(f"  p95: {format_latency_ms(incremental_improvement.get('end_to_end_p95_ms', 0))}")
        print()
        print("  IMPROVEMENT:")
        print(f"    Users see first output {incremental_improvement.get('improvement_factor', 1.0):.2f}x faster")
        print(f"    {incremental_improvement.get('improvement_percent', 0):.1f}% faster time-to-first-segment")
        print(f"    {format_latency_ms(incremental_improvement.get('time_saved_ms', 0))} saved before first output")
        print()
        print("  INTERVIEW TALKING POINTS:")
        print(f"    • First segment available in {format_latency_ms(incremental_improvement.get('time_to_first_segment_p95_ms', 0))} vs {format_latency_ms(incremental_improvement.get('end_to_end_p95_ms', 0))} for full output")
        print(f"    • Users can start consuming content {incremental_improvement.get('improvement_percent', 0):.0f}% faster")
        print(f"    • Reduces perceived latency by {format_latency_ms(incremental_improvement.get('time_saved_ms', 0))}")
    else:
        print("\nINCREMENTAL OUTPUT: No timing data available yet")
        print("  (Process some jobs to see time-to-first-segment metrics)")
    
    print()
    print("=" * 80)
    print("\nTo view raw JSON data, use: curl http://127.0.0.1:8000/v1/metrics")
    print("=" * 80)


async def main():
    """Generate and display performance report."""
    try:
        report = await get_performance_report()
        print_metrics_report(report)
    except Exception as e:
        print(f"Error generating report: {e}", file=sys.stderr)
        print("\nMake sure Redis is running and the app has processed some jobs.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
