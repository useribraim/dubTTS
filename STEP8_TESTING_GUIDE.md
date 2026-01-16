# Step 8: Performance Optimization + Structured Logging - Testing Guide

## What Was Implemented

### 1. **Structured Logging** (`app/logging_config.py`)
- JSON-formatted logs with correlation IDs
- Request/response logging with timing
- Error tracking with stack traces
- Context-aware logging (job_id, segment_index, etc.)

### 2. **Performance Optimization** (`app/performance.py`)
- **Redis-backed translation caching** - Frequent translation pairs are cached
- Cache TTL: 24 hours
- Automatic cache key generation (SHA256 hash)

### 3. **Enhanced Logging Throughout**
- FastAPI middleware for request logging
- Worker logging with job tracking
- Pipeline logging with performance metrics
- Correlation IDs for request tracing

## How to Test the Full End-to-End Workflow

### Step 1: Start All Services

**Terminal 1: Redis**
```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

**Terminal 2: FastAPI Server**
```bash
cd dub_mvp
export REDIS_URL="redis://localhost:6379/0"
export LOG_LEVEL="INFO"  # or DEBUG for more details
export LOG_JSON="0"       # Set to "1" for JSON logs

uvicorn app.main:app --reload
```

**Terminal 3: Worker**
```bash
cd dub_mvp
export REDIS_URL="redis://localhost:6379/0"
export USE_AWS="0"        # Use 0 for local testing (no AWS)
export LOG_LEVEL="INFO"
export LOG_JSON="0"

python -m app.worker
```

### Step 2: Test the Full Workflow

#### Option A: Use the Demo Script (Easiest)

**Terminal 4:**
```bash
cd dub_mvp
python demo_workflow.py
```

This will:
1. Check services are running
2. Find or ask for an audio file
3. Upload and create a job
4. Show job status
5. Stream real-time events
6. Download the final result

#### Option B: Manual Testing

**1. Upload an audio file:**
```bash
curl -F "file=@data/uploads/a0b5944144e649a0b09773c974fd792d_karpathy.wav" \
  "http://127.0.0.1:8000/v1/dubs?src_lang=en&tgt_lang=es&voice=Joanna"
```

**Response:**
```json
{"job_id": "abc123..."}
```

**2. Watch the logs in Terminal 2 (FastAPI):**
You'll see structured logs like:
```
2024-01-01 12:00:00 [INFO] app.main: Request started method=POST path=/v1/dubs correlation_id=xyz
2024-01-01 12:00:00 [INFO] app.main: Creating job job_id=abc123 filename=test.wav
2024-01-01 12:00:00 [INFO] app.main: Job enqueued job_id=abc123
2024-01-01 12:00:00 [INFO] app.main: Request completed status_code=200 duration_ms=45
```

**3. Watch the logs in Terminal 3 (Worker):**
You'll see:
```
2024-01-01 12:00:01 [INFO] app.worker: Worker started redis_url=redis://localhost:6379/0
2024-01-01 12:00:01 [INFO] app.worker: Job picked from queue job_id=abc123
2024-01-01 12:00:01 [INFO] app.pipeline_redis: Starting job processing job_id=abc123
2024-01-01 12:00:02 [INFO] app.pipeline_redis: Audio converted duration_ms=1200
2024-01-01 12:00:03 [INFO] app.pipeline_redis: Segment processed segment_index=0 asr_ms=1500 mt_ms=200 tts_ms=800 total_ms=2500
2024-01-01 12:00:05 [INFO] app.pipeline_redis: Segment processed segment_index=1 asr_ms=1400 mt_ms=5 tts_ms=750 total_ms=2155
2024-01-01 12:00:06 [INFO] app.pipeline_redis: Job completed successfully segments_count=2
```

**Notice:** The second segment's `mt_ms=5` is much faster - that's the **cache working**! The translation was cached from the first segment.

**4. Stream events (SSE):**
```bash
curl -N "http://127.0.0.1:8000/v1/dubs/abc123/events"
```

You'll see real-time events:
```
event: status
data: {"status": "connected"}

event: status
data: {"status": "queued"}

event: status
data: {"status": "running"}

event: segment
data: {"segment_index": 0, "src_text": "...", "tgt_text": "...", "asr_ms": 1500, "mt_ms": 200, "tts_ms": 800, "total_ms": 2500}

event: segment
data: {"segment_index": 1, "src_text": "...", "tgt_text": "...", "asr_ms": 1400, "mt_ms": 5, "tts_ms": 750, "total_ms": 2155}

event: done
data: {"output_path": "/path/to/final.wav"}

event: status
data: {"status": "done"}
```

**5. Check job status:**
```bash
curl "http://127.0.0.1:8000/v1/dubs/abc123"
```

**6. Download final result:**
```bash
curl -o result.wav "http://127.0.0.1:8000/v1/dubs/abc123/result"
```

### Step 3: Test Caching Performance

**Test 1: First Request (Cache Miss)**
```bash
# Upload same file again
curl -F "file=@test.wav" "http://127.0.0.1:8000/v1/dubs?src_lang=en&tgt_lang=es"
# Note the mt_ms timing in logs
```

**Test 2: Second Request (Cache Hit)**
```bash
# Upload same file with same translation pair
curl -F "file=@test.wav" "http://127.0.0.1:8000/v1/dubs?src_lang=en&tgt_lang=es"
# mt_ms should be < 10ms (cache hit!)
```

**Check Redis cache:**
```bash
redis-cli
> KEYS dub:cache:translate:*
> GET dub:cache:translate:<hash>
```

### Step 4: Test Logging Features

#### JSON Logging (for production)
```bash
export LOG_JSON="1"
uvicorn app.main:app --reload
```

Now logs are JSON:
```json
{"timestamp": "2024-01-01T12:00:00", "level": "INFO", "logger": "app.main", "message": "Request started", "correlation_id": "xyz", "method": "POST", "path": "/v1/dubs"}
```

#### Debug Logging
```bash
export LOG_LEVEL="DEBUG"
python -m app.worker
```

You'll see more detailed logs including cache hit/miss information.

### Step 5: Monitor Performance Metrics

**Check Redis for cached translations:**
```bash
redis-cli
> KEYS dub:cache:translate:*
> TTL dub:cache:translate:<hash>  # Should show remaining seconds
```

**Check job queue:**
```bash
redis-cli
> LLEN dub:queue
> LRANGE dub:queue 0 -1
```

**Check job status:**
```bash
redis-cli
> HGETALL dub:job:abc123
```

## What to Look For

### Performance Improvements

1. **Translation Caching:**
   - First translation: `mt_ms` ~150-300ms (AWS call)
   - Cached translation: `mt_ms` < 10ms (Redis cache)
   - **Improvement: 15-30x faster!**

2. **Structured Logging:**
   - All logs include correlation IDs
   - Request timing in milliseconds
   - Error stack traces for debugging

3. **Observability:**
   - Track jobs end-to-end via correlation_id
   - See cache hit rates in logs
   - Monitor performance per segment

### Expected Log Output

**FastAPI (Terminal 2):**
```
2024-01-01 12:00:00 [INFO] app.main: Request started method=POST path=/v1/dubs
2024-01-01 12:00:00 [INFO] app.main: Creating job job_id=abc123
2024-01-01 12:00:00 [INFO] app.main: Job enqueued job_id=abc123
2024-01-01 12:00:00 [INFO] app.main: Request completed status_code=200 duration_ms=45
```

**Worker (Terminal 3):**
```
2024-01-01 12:00:01 [INFO] app.worker: Job picked from queue job_id=abc123
2024-01-01 12:00:01 [INFO] app.pipeline_redis: Starting job processing job_id=abc123
2024-01-01 12:00:02 [INFO] app.pipeline_redis: Audio converted duration_ms=1200
2024-01-01 12:00:03 [INFO] app.pipeline_redis: Segment processed segment_index=0 asr_ms=1500 mt_ms=200 tts_ms=800 total_ms=2500
2024-01-01 12:00:05 [INFO] app.pipeline_redis: Segment processed segment_index=1 asr_ms=1400 mt_ms=5 tts_ms=750 total_ms=2155
2024-01-01 12:00:06 [INFO] app.pipeline_redis: Job completed successfully segments_count=2
```

## Key Features Demonstrated

- **Structured Logging**: All logs include context (job_id, correlation_id, timing)
- **Performance Caching**: Translation pairs cached in Redis (15-30x speedup)
- **Request Tracking**: Correlation IDs track requests across services
- **Error Handling**: Stack traces in logs for debugging
- **Observability**: Performance metrics in every log entry

## Troubleshooting

### Logs not appearing?
- Check `LOG_LEVEL` environment variable
- Verify logging is configured in main.py and worker.py

### Cache not working?
- Check Redis is running
- Verify `REDIS_URL` is set correctly
- Check cache keys in Redis: `redis-cli KEYS dub:cache:*`

### Performance not improving?
- Cache only works for **identical** translation pairs
- Check `mt_ms` in logs - should be < 10ms for cache hits
- First request always misses cache (expected)

## Next Steps

After testing, you can:
1. Add more metrics (p95 latency tracking)
2. Add Prometheus metrics export
3. Add load testing with Locust
4. Implement unit/integration tests

## AWS IAM/Secrets Manager Integration

AWS IAM roles and Secrets Manager integration is now implemented! See `AWS_IAM_SECRETS_SETUP.md` for complete setup instructions.

**Features:**
- IAM role support for EC2/ECS/Lambda (production-ready)
- Secrets Manager integration for secure credential storage
- Automatic credential detection and fallback chain
- Least-privilege security best practices

**Quick Start:**
```bash
# For EC2/ECS (using IAM roles)
export AWS_USE_IAM_ROLE="auto"  # Auto-detect instance metadata
export AWS_REGION="eu-west-1"
export USE_AWS="1"

# For Secrets Manager
export AWS_SECRETS_MANAGER_SECRET_NAME="dub-mvp/aws-credentials"
export AWS_REGION="eu-west-1"
export USE_AWS="1"
```
