# Quick Start - See It Work!

## Start Everything (3 terminals)

### Terminal 1: Redis
```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

### Terminal 2: FastAPI Server
```bash
cd dub_mvp
export REDIS_URL="redis://localhost:6379/0"
uvicorn app.main:app --reload
```

### Terminal 3: Worker
```bash
cd dub_mvp
export REDIS_URL="redis://localhost:6379/0"
export USE_AWS="0"  # Use 0 for local testing (no AWS needed)
# For production, use IAM roles or Secrets Manager (see AWS_IAM_SECRETS_SETUP.md)
python -m app.worker
```

## Run the Interactive Demo

### Terminal 4: Run Demo
```bash
cd dub_mvp
python demo_workflow.py
```

The demo will:
1. Check if services are running
2. Find or ask for an audio file
3. Upload and create a job
4. Show job status
5. Stream real-time events (watch segments being processed!)
6. Download the final dubbed audio

## Manual Testing

### 1. Create a Job
```bash
curl -F "file=@your_audio.wav" \
  "http://127.0.0.1:8000/v1/dubs?src_lang=en&tgt_lang=es&voice=Joanna"
```

**Response:**
```json
{"job_id": "abc123..."}
```

### 2. Stream Events (Watch Progress!)
```bash
curl -N "http://127.0.0.1:8000/v1/dubs/abc123/events"
```

You'll see:
```
event: status
data: {"status": "connected"}

event: status
data: {"status": "queued"}

event: status
data: {"status": "running"}

event: segment
data: {"segment_index": 0, "src_text": "...", "tgt_text": "...", ...}

event: segment
data: {"segment_index": 1, ...}

event: done
data: {"output_path": "/path/to/final.wav"}

event: status
data: {"status": "done"}
```

### 3. Check Status
```bash
curl "http://127.0.0.1:8000/v1/dubs/abc123"
```

### 4. Download Result
```bash
curl -o result.wav "http://127.0.0.1:8000/v1/dubs/abc123/result"
```

## What You'll See

### In the Worker Terminal:
```
[worker] connected to redis: redis://localhost:6379/0
[worker] output_root: /path/to/outputs
[worker] waiting on queue: dub:queue
[worker] picked job abc123...
```

### In the API Terminal:
```
INFO:     127.0.0.1:xxxxx - "POST /v1/dubs HTTP/1.1" 200 OK
INFO:     127.0.0.1:xxxxx - "GET /v1/dubs/abc123/events HTTP/1.1" 200 OK
```

### In the Event Stream:
- Real-time segment processing
- Timing information (ASR, translation, TTS)
- Source and target text
- Audio paths for each segment

## Key Things to Notice

1. **Fast Response**: API responds immediately with job_id (doesn't wait for processing)
2. **Real-time Progress**: SSE stream shows segments as they're completed
3. **Streaming VAD**: First segment appears quickly (not after full audio processing)
4. **Separate Processes**: Worker does heavy work, API stays responsive
5. **State Persistence**: Job state survives restarts (stored in Redis)

## Troubleshooting

### API not responding?
- Check if uvicorn is running
- Check if port 8000 is available
- Look for errors in the terminal

### Worker not picking up jobs?
- Check if Redis is running
- Check REDIS_URL environment variable
- Look for connection errors in worker terminal

### No events in SSE stream?
- Make sure worker is running
- Check Redis Pub/Sub is working
- Verify job_id is correct

### Jobs stuck in "queued"?
- Worker might not be running
- Check worker terminal for errors
- Verify Redis connection

## More Info

- See `HOW_IT_WORKS.md` for detailed architecture explanation
- See `SETUP_STEP7.md` for setup instructions
