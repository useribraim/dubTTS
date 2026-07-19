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

# Redis connection for metrics, one per event loop (clients are loop-bound)
_metrics_redis: Dict[int, redis.Redis] = {}

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

# Stage-level reliability counters (streaming pipeline)
STAGE_RETRY_COUNTER = Counter("dub_stage_retries_total", "Stage executions retried after failure", ["stage"])
STAGE_RECOVERED_COUNTER = Counter("dub_stage_recovered_total", "Tasks that succeeded after at least one retry", ["stage"])
STAGE_SUCCESS_COUNTER = Counter("dub_stage_success_total", "Successful stage executions", ["stage"])
DLQ_COUNTER = Counter("dub_dlq_total", "Tasks dead-lettered after exhausting retries", ["stage"])

# Redis keys for cross-process reliability reporting
RETRY_PREFIX = f"{METRICS_PREFIX}retry:"
RECOVERED_PREFIX = f"{METRICS_PREFIX}recovered:"
SUCCESS_PREFIX = f"{METRICS_PREFIX}success:"
DLQ_PREFIX = f"{METRICS_PREFIX}dlq:"


def _get_metrics_redis() -> redis.Redis:
    """Get or create the metrics Redis connection for the running loop."""
    try:
        loop_id = id(__import__("asyncio").get_running_loop())
    except RuntimeError:
        loop_id = 0
    client = _metrics_redis.get(loop_id)
    if client is None:
        client = redis.from_url(REDIS_URL, decode_responses=True)
        _metrics_redis[loop_id] = client
    return client


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


async def _record_counter(prefix: str, stage: str, prom_counter) -> None:
    """Bump a reliability counter in Redis (cross-process) and Prometheus."""
    try:
        r = _get_metrics_redis()
        key = f"{prefix}{stage}"
        await r.incr(key)
        await r.expire(key, 30 * 24 * 3600)
    except Exception:
        pass
    try:
        prom_counter.labels(stage=stage).inc()
    except Exception:
        pass


async def record_stage_retry(stage: str) -> None:
    """A stage execution failed and is being retried."""
    await _record_counter(RETRY_PREFIX, stage, STAGE_RETRY_COUNTER)


async def record_stage_recovered(stage: str) -> None:
    """A task succeeded after at least one retry."""
    await _record_counter(RECOVERED_PREFIX, stage, STAGE_RECOVERED_COUNTER)


async def record_stage_success(stage: str) -> None:
    """A stage execution succeeded."""
    await _record_counter(SUCCESS_PREFIX, stage, STAGE_SUCCESS_COUNTER)


async def record_dlq(stage: str) -> None:
    """A task exhausted retries and was dead-lettered."""
    await _record_counter(DLQ_PREFIX, stage, DLQ_COUNTER)


async def _read_counter(r: redis.Redis, prefix: str, stage: str) -> int:
    try:
        val = await r.get(f"{prefix}{stage}")
        return int(val) if val else 0
    except Exception:
        return 0


async def get_reliability_stats(stages=("asr", "mt", "tts")) -> Dict[str, Dict[str, float]]:
    """
    Reliability summary per stage: bounded-retry recovery and dead-letter rates.

    recovery_rate = recovered / (recovered + dead_lettered)   # failed tasks rescued
    dlq_rate      = dead_lettered / (successes + dead_lettered)
    """
    try:
        r = _get_metrics_redis()
    except Exception:
        return {}
    stats: Dict[str, Dict[str, float]] = {}
    for stage in stages:
        retries = await _read_counter(r, RETRY_PREFIX, stage)
        recovered = await _read_counter(r, RECOVERED_PREFIX, stage)
        successes = await _read_counter(r, SUCCESS_PREFIX, stage)
        dead = await _read_counter(r, DLQ_PREFIX, stage)
        failed_tasks = recovered + dead
        stats[stage] = {
            "retries": retries,
            "recovered": recovered,
            "successes": successes,
            "dead_lettered": dead,
            "failed_tasks": failed_tasks,
            "recovery_rate": round(recovered / failed_tasks, 4) if failed_tasks else None,
            "dlq_rate": round(dead / (successes + dead), 4) if (successes + dead) else None,
        }
    return stats


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
    reliability_stats = await get_reliability_stats()
    
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
        "reliability": reliability_stats,
        "timestamp": datetime.utcnow().isoformat(),
    }
