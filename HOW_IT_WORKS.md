# How the Dub MVP Application Works

This document explains the architecture and workflow of the Dub MVP microservice application.

## Architecture Overview

```
┌─────────────┐         ┌──────────┐         ┌──────────┐
│   Client    │────────▶│ FastAPI  │────────▶│  Redis   │
│  (Browser/  │         │  Server  │         │  (Store  │
│   curl)     │◀────────│          │◀────────│   Queue  │
└─────────────┘         └──────────┘         └──────────┘
                              │                    │
                              │                    │
                              ▼                    ▼
                        ┌──────────┐         ┌──────────┐
                        │   SSE    │         │  Worker  │
                        │  Stream  │         │ Process  │
                        └──────────┘         └──────────┘
```

## Components

### 1. **FastAPI Server** (`app/main.py)
- **Role**: Lightweight web server (control plane)
- **Responsibilities**:
  - Receives file uploads
  - Creates jobs in Redis
  - Enqueues jobs to Redis queue
  - Streams events via Server-Sent Events (SSE)
  - Serves final results and segments
- **Key Endpoints**:
  - `POST /v1/dubs` - Upload file, create job
  - `GET /v1/dubs/{job_id}` - Get job status
  - `GET /v1/dubs/{job_id}/events` - SSE event stream
  - `GET /v1/dubs/{job_id}/result` - Download final audio
  - `GET /v1/dubs/{job_id}/segments/{i}` - Get individual segment

### 2. **Worker Process** (`app/worker.py`)
- **Role**: Background processor (data plane)
- **Responsibilities**:
  - Pulls jobs from Redis queue (BLPOP)
  - Processes audio files (ASR → Translate → TTS)
  - Publishes progress events to Redis Pub/Sub
  - Updates job status in Redis
- **Key Features**:
  - Runs in separate process/container
  - Doesn't block FastAPI server
  - Can scale horizontally (multiple workers)

### 3. **Redis** (External Service)
- **Role**: State management and messaging
- **Data Structures**:
  - **Hash**: `dub:job:{job_id}` - Job metadata (status, paths, languages, etc.)
  - **List**: `dub:job:{job_id}:segments` - List of segment file paths
  - **List**: `dub:queue` - Job queue (FIFO)
  - **Pub/Sub**: `dub:job:{job_id}:events` - Event channel for SSE

## Complete Workflow

### Step 1: Upload & Create Job
```
Client → POST /v1/dubs (with audio file)
  ↓
FastAPI:
  1. Saves file to disk
  2. Generates unique job_id
  3. Creates job record in Redis (status: "queued")
  4. Enqueues job_id to Redis queue
  5. Publishes "queued" event
  6. Returns job_id to client
```

### Step 2: Worker Picks Up Job
```
Worker (BLPOP on dub:queue):
  1. Blocks until job available
  2. Receives job_id
  3. Updates job status to "running"
  4. Publishes "running" event
```

### Step 3: Audio Processing Pipeline
```
Worker processes job:
  
  ┌─────────────────────────────────────────┐
  │ 1. Convert to 16kHz mono WAV (ffmpeg)   │
  └─────────────────────────────────────────┘
                    ↓
  ┌─────────────────────────────────────────┐
  │ 2. Streaming VAD Segmentation           │
  │    • Detects speech segments in real-time│
  │    • Yields segments as they're found   │
  │    • Faster time-to-first-segment!      │
  └─────────────────────────────────────────┘
                    ↓
  ┌─────────────────────────────────────────┐
  │ 3. For each segment (streaming):         │
  │    a. ASR (faster-whisper)              │
  │    b. Translate (AWS Translate)          │
  │    c. TTS (AWS Polly)                    │
  │    d. Save segment WAV                   │
  │    e. Append to Redis segment list      │
  │    f. Publish "segment" event           │
  └─────────────────────────────────────────┘
                    ↓
  ┌─────────────────────────────────────────┐
  │ 4. Concatenate all segments (ffmpeg)    │
  └─────────────────────────────────────────┘
                    ↓
  ┌─────────────────────────────────────────┐
  │ 5. Update job status to "done"          │
  │    Publish "done" event                 │
  └─────────────────────────────────────────┘
```

### Step 4: Client Receives Events (SSE)
```
Client → GET /v1/dubs/{job_id}/events
  ↓
FastAPI:
  1. Subscribes to Redis Pub/Sub channel
  2. Streams events to client as they arrive:
     - "status" events (queued/running/done)
     - "segment" events (with timing, text, audio_path)
     - "done" event (with final output path)
  3. Sends heartbeat every 10 seconds
```

### Step 5: Download Result
```
Client → GET /v1/dubs/{job_id}/result
  ↓
FastAPI:
  1. Checks job status in Redis
  2. Returns final WAV file if ready
```

## Key Features

### 1. **Streaming VAD Segmentation**
- Traditional approach: Process entire audio → return all segments
- **Our approach**: Yield segments as soon as speech ends are detected
- **Benefit**: Faster time-to-first-segment (user sees progress sooner)

### 2. **Redis Pub/Sub for SSE**
- Events published to Redis channels
- Multiple clients can subscribe to same job
- Works across different processes/containers
- FastAPI just forwards Redis messages to HTTP clients

### 3. **Separate Worker Process**
- FastAPI stays responsive (no blocking CPU work)
- Worker can be scaled independently
- Worker failures don't crash the web server
- Can run on different machines

### 4. **State Persistence**
- All job state in Redis
- Survives restarts (jobs can be resumed)
- Works across multiple API instances
- No in-memory state that gets lost

## Event Flow Example

When processing a 30-second audio file with 3 speech segments:

```
Time    Event Type    Data
─────────────────────────────────────────────────────────
0.0s    status        {"status": "queued"}
0.1s    status        {"status": "running"}
2.5s    segment       {
         "segment_index": 0,
         "start_ms": 0,
         "end_ms": 8500,
         "src_text": "Hello, this is the first segment...",
         "tgt_text": "Hola, este es el primer segmento...",
         "audio_path": "/path/to/dub_0000.wav",
         "asr_ms": 1200,
         "mt_ms": 150,
         "tts_ms": 800,
         "total_ms": 2150
       }
8.0s    segment       {
         "segment_index": 1,
         "start_ms": 8500,
         "end_ms": 16500,
         ...
       }
15.0s   segment       {
         "segment_index": 2,
         "start_ms": 16500,
         "end_ms": 25000,
         ...
       }
18.0s   done          {"output_path": "/path/to/final.wav"}
18.0s   status        {"status": "done"}
```

## Running the Application

### Terminal 1: Redis
```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

### Terminal 2: FastAPI
```bash
cd dub_mvp
export REDIS_URL="redis://localhost:6379/0"
uvicorn app.main:app --reload
```

### Terminal 3: Worker
```bash
cd dub_mvp
export REDIS_URL="redis://localhost:6379/0"
export USE_AWS="1"  # or "0" for fallback
export AWS_REGION="eu-west-1"
python -m app.worker
```

### Terminal 4: Test/Demo
```bash
cd dub_mvp
python demo_workflow.py
```

## Why This Architecture?

1. **Scalability**: Can run multiple API instances + multiple workers
2. **Resilience**: State in Redis survives crashes
3. **Responsiveness**: Web server never blocks on CPU work
4. **Observability**: Real-time events show exactly what's happening
5. **Flexibility**: Easy to add more workers, change processing logic, etc.

## Debugging Tips

- Check Redis keys: `redis-cli KEYS "dub:*"`
- Monitor queue: `redis-cli LLEN dub:queue`
- Watch Pub/Sub: `redis-cli MONITOR`
- Check job status: `curl http://127.0.0.1:8000/v1/dubs/{job_id}`
- Stream events: `curl -N http://127.0.0.1:8000/v1/dubs/{job_id}/events`
