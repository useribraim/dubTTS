## Performance Results (Snapshot)

Date: 2026-01-15
Source: /v1/metrics (Redis-backed aggregates)

### Job-Level Latency
- End-to-end p50: 27.6s
- End-to-end p95: 92.3s
- Time-to-first-segment p50: 5.18s
- Time-to-first-segment p95: 5.89s

### Stage Latency (Per Segment)
- ASR p50: 2.04s, p95: 3.47s
- Translate p50: 12.9ms, p95: 448ms
- TTS p50: 283ms, p95: 1.16s
- Segment total p50: 2.48s, p95: 4.56s

### Cache Impact (Translate)
- Hit rate: 49.8%
- Cache hit p50: 2.48ms
- Cache miss p50: 162ms
- Avg latency with cache: 82.6ms
- Improvement factor: 1.96x (about 49% reduction)

### Streaming Benefit (Perceived Latency)
- TTFS p95: 5.89s vs end-to-end p95: 92.3s
- Perceived latency reduction: 93.6%
- Time saved before first output: 86.4s

### How to Reproduce
- JSON metrics: curl http://127.0.0.1:8000/v1/metrics | python3 -m json.tool
- Worker Prometheus: curl http://127.0.0.1:9108/metrics

Notes
- Values are from 7 job samples in Redis at capture time.
- Translate cache hit/miss computed from Redis cache stats.
