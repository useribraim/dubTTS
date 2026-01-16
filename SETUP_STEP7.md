# Step 7: Redis-Backed Microservice Setup

This guide covers setting up the Redis-backed microservice architecture for the Dub MVP.

## Prerequisites

- Docker (for running Redis)
- Python 3.8+ with dependencies installed
- Redis Python package (already in requirements.txt)

## Step 1: Start Redis

Run Redis using Docker:

```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

Or if you have Redis installed locally:

```bash
redis-server
```

## Step 2: Set Environment Variables

```bash
export REDIS_URL="redis://localhost:6379/0"
export USE_AWS="1"          # or 0 for fallback/no AWS
export AWS_REGION="eu-west-1"
```

## Step 3: Install Dependencies

Redis is already in the requirements.txt. If you need to install:

```bash
pip install redis
```

Or install all dependencies:

```bash
pip install -r ../requirements.txt
```

## Step 4: Run the Application

### Terminal A - FastAPI Server

```bash
cd dub_mvp
uvicorn app.main:app --reload
```

### Terminal B - Worker Process

```bash
cd dub_mvp
python -m app.worker
```

## Step 5: Test the API

### Create a job:

```bash
curl -F "file=@sample.mp4" "http://127.0.0.1:8000/v1/dubs?src_lang=en&tgt_lang=es&voice=Joanna"
```

This returns a `job_id`.

### Stream events (SSE):

```bash
curl -N "http://127.0.0.1:8000/v1/dubs/<JOB_ID>/events"
```

You should see:
- `status` events (queued/running/done)
- Multiple `segment` events (each with playable audio_path)
- `done` event when complete

### Get final result:

```bash
curl "http://127.0.0.1:8000/v1/dubs/<JOB_ID>/result" -o final.wav
```

## Architecture Overview

- **FastAPI** (main.py): Lightweight web server that handles uploads, creates jobs, and streams events
- **Worker** (worker.py): Separate process that pulls jobs from Redis queue and processes them
- **Redis**: 
  - Job store (persistent state)
  - Job queue (BLPOP-based)
  - Pub/Sub event bus (for SSE streaming)

## Key Features

- **State survives restarts**: Jobs stored in Redis
- **Works across replicas**: Multiple API instances can share Redis
- **Separate worker process**: CPU work doesn't block web server
- **Streaming VAD**: Segments processed as soon as detected (faster time-to-first-segment)
- **Real-time events**: SSE stream shows progress as segments are completed
