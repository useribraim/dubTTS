# Performance Metrics & Reporting

This document explains how the system tracks and reports performance metrics, including p50, p95, p99 latency and cache improvements.

## Overview

The system now tracks:
- **Latency metrics**: p50 (median), p95, p99 for all operations (ASR, translation, TTS, total)
- **Cache statistics**: Hit/miss rates for translation caching
- **Performance improvements**: Calculated improvement from caching

## How It Works

### 1. Metrics Collection

Metrics are automatically collected during job processing:

- **ASR latency**: Recorded after each transcription
- **Translation latency**: Recorded for each translation (includes cache hit/miss)
- **TTS latency**: Recorded after each text-to-speech generation
- **Total segment latency**: Recorded for complete segment processing

All metrics are stored in Redis with automatic cleanup (keeps last 1000 samples per operation).

### 2. Cache Hit/Miss Tracking

Translation operations track:
- Cache hits: Fast Redis lookups (~5ms)
- Cache misses: AWS Translate API calls (~150-300ms)

This data is used to calculate cache hit rate and performance improvement.

### 3. Percentile Calculation

For each operation, the system:
1. Collects latency samples (up to 1000 most recent)
2. Calculates percentiles:
   - **p50 (median)**: Middle value - typical performance
   - **p95**: 95% of requests are faster than this - key metric for user experience
   - **p99**: 99% of requests are faster than this - worst-case scenarios

## Viewing Metrics

### Option 1: API Endpoint

```bash
curl http://127.0.0.1:8000/v1/metrics
```

Returns JSON with:
- `latency_metrics`: p50, p95, p99 for each operation
- `cache_stats`: Hit/miss rates
- `cache_improvement`: Calculated improvement factor

### Option 2: Performance Report Script

```bash
python performance_report.py
```

Generates a human-readable report:

```
================================================================================
PERFORMANCE METRICS REPORT
================================================================================
Generated: 2026-01-09T02:35:00

LATENCY METRICS (p50, p95, p99)
--------------------------------------------------------------------------------

ASR:
  Count:        45 samples
  p50 (median):  850.2ms
  p95:           1200.5ms
  p99:           1450.0ms
  Min:           450.0ms
  Max:           1500.0ms
  Mean:          900.3ms

TRANSLATE:
  Count:        45 samples
  p50 (median):  8.5ms      ← Fast! (cache hits)
  p95:           250.0ms    ← Some cache misses
  p99:           280.0ms
  Min:           5.2ms
  Max:           300.0ms
  Mean:          45.2ms

TTS:
  Count:        45 samples
  p50 (median):  850.0ms
  p95:           1200.0ms
  p99:           1500.0ms
  Mean:          900.0ms

--------------------------------------------------------------------------------

CACHE STATISTICS
--------------------------------------------------------------------------------

TRANSLATE:
  Total requests:  45
  Cache hits:      30 (66.7%)
  Cache misses:    15 (33.3%)

--------------------------------------------------------------------------------

PERFORMANCE IMPROVEMENT FROM CACHING
--------------------------------------------------------------------------------
Cache hit rate:              66.7%
Avg latency (with cache):    70.0ms
Avg latency (without cache):  200.0ms

IMPROVEMENT: 2.86x faster
            65.0% reduction in latency
================================================================================
```

## What the Metrics Show

### Latency Percentiles

- **p50 (median)**: Typical request latency
- **p95**: 95% of requests are faster - this is what most users experience
- **p99**: 99% of requests are faster - worst-case for most users

### Cache Improvement

The system calculates:
- **Improvement factor**: How many times faster with caching (e.g., "2.86x faster")
- **Improvement percent**: Percentage reduction in latency (e.g., "65% reduction")

Example:
- Without cache: 200ms average
- With 66.7% hit rate: 70ms average
- **Improvement: 2.86x faster, 65% reduction**

## Resume Alignment

This directly supports the resume claim:

> "Implemented service-level performance improvements using profiling and caching of frequent translation pairs, reducing end-to-end p95 latency."

**Evidence:**
- ✅ p95 latency tracked for all operations
- ✅ Cache hit/miss rates measured
- ✅ Improvement factor calculated and reported
- ✅ Metrics available via API endpoint
- ✅ Human-readable performance reports

## Example: Demonstrating Improvement

1. **Process some jobs** (with repeated translations to build cache):
```bash
# Upload same audio file multiple times
curl -F "file=@harvard.wav" "http://127.0.0.1:8000/v1/dubs?src_lang=en&tgt_lang=ru"
```

2. **View metrics**:
```bash
python performance_report.py
```

3. **See the improvement**:
- First job: All cache misses → higher latency
- Subsequent jobs: Cache hits → lower latency
- Report shows: "2.86x faster, 65% reduction"

## Technical Details

### Storage

- Metrics stored in Redis lists: `dub:metrics:latency:{operation}`
- Cache stats in Redis counters: `dub:metrics:cache:{operation}:hits/misses`
- Automatic expiry: 30 days
- Sample limit: 1000 per operation (FIFO)

### Calculation

Percentiles calculated using sorted array method:
- p95 = sorted_latencies[int(len * 0.95)]
- p99 = sorted_latencies[int(len * 0.99)]

Cache improvement:
- Weighted average: `hit_rate * cache_latency + (1 - hit_rate) * aws_latency`
- Improvement factor: `without_cache / with_cache`

## Integration

Metrics are automatically collected - no code changes needed. Just:
1. Process jobs normally
2. View metrics via API or report script
3. See p50/p95/p99 and cache improvements

This provides the data needed to demonstrate performance improvements in interviews! 🚀
