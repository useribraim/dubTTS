"""
Performance metrics aggregation and reporting.

Tracks latency metrics (p50, p95, p99) and cache performance
to demonstrate performance improvements.
"""

import statistics
from typing import Dict, List, Optional
from datetime import datetime
import redis.asyncio as redis
from prometheus_client import Counter, Gauge, Histogram, generate_latest

from app.redis_backend import REDIS_URL

# Redis connection for metrics
_metrics_redis: Optional[redis.Redis] = None

# Metric key prefixes
METRICS_PREFIX = "dub:metrics:"
LATENCY_LIST_PREFIX = f"{METRICS_PREFIX}latency:"
CACHE_STATS_PREFIX = f"{METRICS_PREFIX}cache:"

# How many samples to keep for percentile calculation
MAX_SAMPLES = 1000

# Prometheus metrics
JOB_LATENCY_MS = Histogram(
    "dub_job_latency_ms",
    "End-to-end job latency in ms",
    buckets=(1000, 2000, 5000, 10000, 20000, 40000, 60000, 120000, 300000),
)
TTFS_MS = Histogram(
    "dub_time_to_first_segment_ms",
    "Time to first segment in ms",
    buckets=(500, 1000, 2000, 5000, 10000, 20000, 40000),
)
STAGE_LATENCY_MS = Histogram(
    "dub_stage_latency_ms",
    "Per-stage latency in ms",
    ["stage"],
    buckets=(50, 100, 250, 500, 1000, 2000, 5000, 10000),
)
QUEUE_DEPTH = Gauge("dub_queue_depth", "Jobs pending in queue")
ACTIVE_JOBS = Gauge("dub_active_jobs", "Jobs currently processing")
RETRY_COUNTER = Counter("dub_job_retries_total", "Total job retries")
FAILURE_COUNTER = Counter("dub_job_failures_total", "Total job failures")
CACHE_HIT_COUNTER = Counter("dub_cache_hits_total", "Cache hits", ["operation"])
CACHE_MISS_COUNTER = Counter("dub_cache_misses_total", "Cache misses", ["operation"])


def _get_metrics_redis() -> redis.Redis:
    """Get or create metrics Redis connection."""
    global _metrics_redis
    if _metrics_redis is None:
        _metrics_redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _metrics_redis


async def record_latency(operation: str, latency_ms: float) -> None:
    """
    Record a latency measurement for an operation.
    
    Args:
        operation: Operation name (e.g., "asr", "translate", "tts", "total")
        latency_ms: Latency in milliseconds
    """
    try:
        r = _get_metrics_redis()
        key = f"{LATENCY_LIST_PREFIX}{operation}"
        
        # Add to list (left push for FIFO)
        await r.lpush(key, str(latency_ms))
        
        # Trim list to keep only last MAX_SAMPLES
        await r.ltrim(key, 0, MAX_SAMPLES - 1)
        
        # Set expiry (30 days)
        await r.expire(key, 30 * 24 * 3600)
    except Exception:
        # Metrics collection failure shouldn't break the app
        pass
    try:
        STAGE_LATENCY_MS.labels(stage=operation).observe(latency_ms)
    except Exception:
        pass


async def record_cache_hit(operation: str, hit: bool, latency_ms: float = None) -> None:
    """
    Record a cache hit or miss with latency.
    
    Args:
        operation: Operation name (e.g., "translate")
        hit: True if cache hit, False if cache miss
        latency_ms: Latency in milliseconds (for comparison)
    """
    try:
        r = _get_metrics_redis()
        hits_key = f"{CACHE_STATS_PREFIX}{operation}:hits"
        misses_key = f"{CACHE_STATS_PREFIX}{operation}:misses"
        
        if hit:
            await r.incr(hits_key)
            # Track cache hit latencies separately for comparison
            if latency_ms is not None:
                hit_latency_key = f"{CACHE_STATS_PREFIX}{operation}:hit_latencies"
                await r.lpush(hit_latency_key, str(latency_ms))
                await r.ltrim(hit_latency_key, 0, MAX_SAMPLES - 1)
                await r.expire(hit_latency_key, 30 * 24 * 3600)
        else:
            await r.incr(misses_key)
            # Track cache miss latencies separately for comparison
            if latency_ms is not None:
                miss_latency_key = f"{CACHE_STATS_PREFIX}{operation}:miss_latencies"
                await r.lpush(miss_latency_key, str(latency_ms))
                await r.ltrim(miss_latency_key, 0, MAX_SAMPLES - 1)
                await r.expire(miss_latency_key, 30 * 24 * 3600)
        
        # Set expiry (30 days)
        await r.expire(hits_key, 30 * 24 * 3600)
        await r.expire(misses_key, 30 * 24 * 3600)
    except Exception:
        pass
    try:
        if hit:
            CACHE_HIT_COUNTER.labels(operation=operation).inc()
        else:
            CACHE_MISS_COUNTER.labels(operation=operation).inc()
    except Exception:
        pass


def _calculate_percentiles(latencies: List[float]) -> Dict[str, float]:
    """Calculate p50, p95, p99 percentiles from a list of latencies."""
    if not latencies:
        return {
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "count": 0,
        }
    
    sorted_latencies = sorted(latencies)
    n = len(sorted_latencies)
    
    def percentile_index(p: float) -> int:
        """Calculate index for percentile p (0.0 to 1.0)."""
        return min(int(n * p), n - 1)
    
    return {
        "p50": statistics.median(sorted_latencies),
        "p95": sorted_latencies[percentile_index(0.95)],
        "p99": sorted_latencies[percentile_index(0.99)],
        "min": min(sorted_latencies),
        "max": max(sorted_latencies),
        "mean": statistics.mean(sorted_latencies),
        "count": n,
    }


async def get_latency_metrics(operation: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    """
    Get latency metrics (p50, p95, p99) for operations.
    
    Args:
        operation: Specific operation name, or None for all operations
    
    Returns:
        Dictionary mapping operation names to percentile metrics
    """
    try:
        r = _get_metrics_redis()
        if operation is None:
            pattern = f"{LATENCY_LIST_PREFIX}*"
        else:
            pattern = f"{LATENCY_LIST_PREFIX}{operation}"
        
        keys = await r.keys(pattern)
        metrics = {}
        
        for key in keys:
            # Extract operation name from key
            op_name = key.replace(LATENCY_LIST_PREFIX, "")
            
            # Get all latency values
            latency_strings = await r.lrange(key, 0, -1)
            latencies = [float(x) for x in latency_strings if x]
            
            if latencies:
                metrics[op_name] = _calculate_percentiles(latencies)
        
        return metrics
    except Exception:
        return {}


async def get_cache_stats(operation: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """
    Get cache hit/miss statistics with actual latency comparisons.
    
    Args:
        operation: Specific operation name, or None for all operations
    
    Returns:
        Dictionary mapping operation names to cache stats with latency comparisons
    """
    try:
        r = _get_metrics_redis()
        pattern = f"{CACHE_STATS_PREFIX}*:hits" if operation is None else f"{CACHE_STATS_PREFIX}{operation}:hits"
        
        hit_keys = await r.keys(pattern)
        stats = {}
        
        for hit_key in hit_keys:
            # Extract operation name
            op_name = hit_key.replace(CACHE_STATS_PREFIX, "").replace(":hits", "")
            miss_key = f"{CACHE_STATS_PREFIX}{op_name}:misses"
            hit_latency_key = f"{CACHE_STATS_PREFIX}{op_name}:hit_latencies"
            miss_latency_key = f"{CACHE_STATS_PREFIX}{op_name}:miss_latencies"
            
            hits = int(await r.get(hit_key) or 0)
            misses = int(await r.get(miss_key) or 0)
            total = hits + misses
            
            if total > 0:
                stat = {
                    "hits": hits,
                    "misses": misses,
                    "total": total,
                    "hit_rate": hits / total if total > 0 else 0.0,
                    "miss_rate": misses / total if total > 0 else 0.0,
                }
                
                # Get actual latency metrics for cache hits vs misses
                hit_latencies_str = await r.lrange(hit_latency_key, 0, -1)
                miss_latencies_str = await r.lrange(miss_latency_key, 0, -1)
                
                if hit_latencies_str:
                    hit_latencies = [float(x) for x in hit_latencies_str if x]
                    if hit_latencies:
                        stat["cache_hit_latency"] = _calculate_percentiles(hit_latencies)
                
                if miss_latencies_str:
                    miss_latencies = [float(x) for x in miss_latencies_str if x]
                    if miss_latencies:
                        stat["cache_miss_latency"] = _calculate_percentiles(miss_latencies)
                
                stats[op_name] = stat
        
        return stats
    except Exception:
        return {}


async def record_job_timing(job_id: str, timing_type: str, timestamp_ms: float) -> None:
    """
    Record job-level timing metrics.
    
    Args:
        job_id: Job identifier
        timing_type: "time_to_first_segment", "end_to_end", etc.
        timestamp_ms: Timestamp in milliseconds
    """
    try:
        r = _get_metrics_redis()
        key = f"dub:metrics:job_timing:{timing_type}"
        await r.lpush(key, str(timestamp_ms))
        await r.ltrim(key, 0, MAX_SAMPLES - 1)
        await r.expire(key, 30 * 24 * 3600)
    except Exception:
        pass
    try:
        if timing_type == "end_to_end":
            JOB_LATENCY_MS.observe(timestamp_ms)
        elif timing_type == "time_to_first_segment":
            TTFS_MS.observe(timestamp_ms)
    except Exception:
        pass


def set_queue_depth(depth: int) -> None:
    try:
        QUEUE_DEPTH.set(depth)
    except Exception:
        pass


def set_active_jobs(count: int) -> None:
    try:
        ACTIVE_JOBS.set(count)
    except Exception:
        pass


def increment_retry() -> None:
    try:
        RETRY_COUNTER.inc()
    except Exception:
        pass


def increment_failure() -> None:
    try:
        FAILURE_COUNTER.inc()
    except Exception:
        pass


def get_prometheus_metrics() -> bytes:
    return generate_latest()


async def get_job_timing_metrics() -> Dict[str, Dict[str, float]]:
    """Get job-level timing metrics (time-to-first-segment, end-to-end)."""
    try:
        r = _get_metrics_redis()
        pattern = "dub:metrics:job_timing:*"
        keys = await r.keys(pattern)
        metrics = {}
        
        for key in keys:
            timing_type = key.replace("dub:metrics:job_timing:", "")
            timings_str = await r.lrange(key, 0, -1)
            timings = [float(x) for x in timings_str if x]
            
            if timings:
                metrics[timing_type] = _calculate_percentiles(timings)
        
        return metrics
    except Exception:
        return {}


async def get_performance_report() -> Dict:
    """
    Generate a comprehensive performance report with before/after comparisons.
    
    Returns:
        Dictionary with latency metrics, cache stats, and improvement calculations
    """
    latency_metrics = await get_latency_metrics()
    cache_stats = await get_cache_stats()
    job_timing_metrics = await get_job_timing_metrics()
    
    # Calculate cache improvement using ACTUAL measured latencies
    cache_improvement = {}
    if "translate" in cache_stats:
        translate_stats = cache_stats["translate"]
        hit_rate = translate_stats.get("hit_rate", 0.0)
        
        # Use actual measured latencies if available
        hit_latency = translate_stats.get("cache_hit_latency", {})
        miss_latency = translate_stats.get("cache_miss_latency", {})
        
        if hit_latency and miss_latency:
            # Use actual p50 (median) latencies
            actual_cache_latency_ms = hit_latency.get("p50", 5.0)
            actual_aws_latency_ms = miss_latency.get("p50", 200.0)
        else:
            # Fallback to estimates if no data yet
            actual_cache_latency_ms = 5.0
            actual_aws_latency_ms = 200.0
        
        # Calculate weighted average latency with cache
        avg_latency_with_cache = (
            hit_rate * actual_cache_latency_ms +
            (1 - hit_rate) * actual_aws_latency_ms
        )
        avg_latency_without_cache = actual_aws_latency_ms
        
        if avg_latency_without_cache > 0 and avg_latency_with_cache > 0:
            improvement_factor = avg_latency_without_cache / avg_latency_with_cache
            improvement_percent = (1 - avg_latency_with_cache / avg_latency_without_cache) * 100
        else:
            improvement_factor = 1.0
            improvement_percent = 0.0
        
        cache_improvement = {
            "hit_rate": hit_rate,
            "cache_hit_p50_ms": round(actual_cache_latency_ms, 2),
            "cache_miss_p50_ms": round(actual_aws_latency_ms, 2),
            "avg_latency_with_cache_ms": round(avg_latency_with_cache, 2),
            "avg_latency_without_cache_ms": round(avg_latency_without_cache, 2),
            "improvement_factor": round(improvement_factor, 2),
            "improvement_percent": round(improvement_percent, 1),
            "cache_hit_latency": hit_latency,
            "cache_miss_latency": miss_latency,
        }
    
    # Calculate incremental output improvement
    incremental_improvement = {}
    if "time_to_first_segment" in job_timing_metrics and "end_to_end" in job_timing_metrics:
        first_segment = job_timing_metrics["time_to_first_segment"]
        end_to_end = job_timing_metrics["end_to_end"]
        
        first_segment_p95 = first_segment.get("p95", 0)
        end_to_end_p95 = end_to_end.get("p95", 0)
        
        if end_to_end_p95 > 0:
            improvement_percent = (1 - first_segment_p95 / end_to_end_p95) * 100
            improvement_factor = end_to_end_p95 / first_segment_p95 if first_segment_p95 > 0 else 1.0
        else:
            improvement_percent = 0.0
            improvement_factor = 1.0
        
        incremental_improvement = {
            "time_to_first_segment_p95_ms": round(first_segment_p95, 2),
            "end_to_end_p95_ms": round(end_to_end_p95, 2),
            "improvement_percent": round(improvement_percent, 1),
            "improvement_factor": round(improvement_factor, 2),
            "time_saved_ms": round(end_to_end_p95 - first_segment_p95, 2),
        }
    
    return {
        "latency_metrics": latency_metrics,
        "cache_stats": cache_stats,
        "cache_improvement": cache_improvement,
        "job_timing_metrics": job_timing_metrics,
        "incremental_improvement": incremental_improvement,
        "timestamp": datetime.utcnow().isoformat(),
    }
