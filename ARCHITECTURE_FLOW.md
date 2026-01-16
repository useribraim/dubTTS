# Application Flow - Visual Guide

## Complete Request Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLIENT REQUEST                                │
│  POST /v1/dubs (with audio file)                                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FASTAPI SERVER                                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 1. Receive file upload                                    │   │
│  │ 2. Save to disk: data/uploads/{job_id}_{filename}        │   │
│  │ 3. Generate job_id (UUID)                                 │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ RedisJobStore.create_job(job_id, {...})                  │   │
│  │   → Redis HSET dub:job:{job_id}                          │   │
│  │     • status: "queued"                                   │   │
│  │     • upload_path: "/path/to/file"                       │   │
│  │     • src_lang: "en"                                     │   │
│  │     • tgt_lang: "es"                                     │   │
│  │     • voice: "Joanna"                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ RedisJobStore.enqueue(job_id)                            │   │
│  │   → Redis RPUSH dub:queue {job_id}                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ RedisEventBus.publish(job_id, "status", {...})          │   │
│  │   → Redis PUBLISH dub:job:{job_id}:events {...}          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  Return: {"job_id": "abc123..."}                                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
                    Client receives job_id
```

## Worker Processing Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    WORKER PROCESS                               │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ while True:                                               │   │
│  │   job_id = await redis.BLPOP("dub:queue")                │   │
│  │   # Blocks until job available                            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ process_job_redis(store, bus, job_id, output_root)       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 1. Update status: "running"                              │   │
│  │ 2. Publish "running" event                              │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Convert audio to 16kHz mono WAV                          │   │
│  │   ffmpeg -i input.mp4 -ac 1 -ar 16000 input.wav         │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Streaming VAD Segmentation                                │   │
│  │   vad_segment_wav_stream()                                │   │
│  │   • Analyzes audio frame-by-frame                         │   │
│  │   • Detects speech start/end                              │   │
│  │   • YIELDS segments as they're found (not all at once!)   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ FOR EACH SEGMENT (streaming):                            │   │
│  │                                                           │   │
│  │   ┌─────────────────────────────────────────────────┐   │   │
│  │   │ a) ASR (Automatic Speech Recognition)           │   │   │
│  │   │    faster-whisper → "Hello world"               │   │   │
│  │   └─────────────────────────────────────────────────┘   │   │
│  │                           │                               │   │
│  │                           ▼                               │   │
│  │   ┌─────────────────────────────────────────────────┐   │   │
│  │   │ b) Translate                                    │   │   │
│  │   │    AWS Translate → "Hola mundo"                │   │   │
│  │   └─────────────────────────────────────────────────┘   │   │
│  │                           │                               │   │
│  │                           ▼                               │   │
│  │   ┌─────────────────────────────────────────────────┐   │   │
│  │   │ c) TTS (Text-to-Speech)                         │   │   │
│  │   │    AWS Polly → audio WAV                        │   │   │
│  │   └─────────────────────────────────────────────────┘   │   │
│  │                           │                               │   │
│  │                           ▼                               │   │
│  │   ┌─────────────────────────────────────────────────┐   │   │
│  │   │ d) Save segment: dub_0000.wav                   │   │   │
│  │   │ e) Append to Redis: RPUSH segments list        │   │   │
│  │   │ f) Publish "segment" event                      │   │   │
│  │   └─────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Concatenate all segments                                 │   │
│  │   ffmpeg -f concat -i list.txt -c copy final.wav        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Update status: "done"                                    │   │
│  │ Publish "done" event                                     │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## SSE Event Streaming Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLIENT                                        │
│  GET /v1/dubs/{job_id}/events                                    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FASTAPI SERVER                                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 1. Verify job exists in Redis                            │   │
│  │ 2. Create Redis PubSub connection                       │   │
│  │ 3. Subscribe to: dub:job:{job_id}:events                │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Send initial: "event: status\ndata: {connected}\n\n"    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ while True:                                               │   │
│  │   msg = await pubsub.get_message(timeout=1.0)            │   │
│  │   if msg:                                                 │   │
│  │   │   yield "event: {type}\ndata: {data}\n\n"            │   │
│  │   else:                                                   │   │
│  │   │   yield "event: heartbeat\ndata: {}\n\n"            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│                           ▼                                       │
│                    Stream to client                               │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
                    Client receives events:
                    • status: queued/running/done
                    • segment: {index, text, audio_path, timing}
                    • done: {output_path}
```

## Redis Data Structures

```
Redis Keys:

┌─────────────────────────────────────────────────────────────┐
│ dub:job:{job_id}                                            │
│ (Hash)                                                       │
│   job_id: "abc123"                                          │
│   status: "running"                                         │
│   created_at: "2024-01-01T12:00:00"                        │
│   updated_at: "2024-01-01T12:05:00"                        │
│   upload_path: "/data/uploads/abc123_file.wav"            │
│   output_path: "/data/outputs/abc123/final.wav"            │
│   src_lang: "en"                                            │
│   tgt_lang: "es"                                            │
│   voice: "Joanna"                                           │
│   error: ""                                                  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ dub:job:{job_id}:segments                                   │
│ (List)                                                       │
│   [0] "/data/outputs/abc123/segments_out/dub_0000.wav"    │
│   [1] "/data/outputs/abc123/segments_out/dub_0001.wav"    │
│   [2] "/data/outputs/abc123/segments_out/dub_0002.wav"    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ dub:queue                                                    │
│ (List - FIFO Queue)                                          │
│   ["job_id_1", "job_id_2", "job_id_3"]                     │
│   Worker uses BLPOP to get jobs (blocks until available)    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ dub:job:{job_id}:events                                     │
│ (Pub/Sub Channel)                                           │
│   Messages published by worker:                             │
│   • {"type": "status", "data": {"status": "running"}}      │
│   • {"type": "segment", "data": {...}}                     │
│   • {"type": "done", "data": {"output_path": "..."}}      │
└─────────────────────────────────────────────────────────────┘
```

## Timeline Example (30-second audio, 3 segments)

```
Time    Component    Action
─────────────────────────────────────────────────────────────
0.0s    Client       POST /v1/dubs (upload file)
0.1s    FastAPI      Create job in Redis, enqueue
0.2s    FastAPI      Return job_id to client
0.3s    Worker       BLPOP picks up job_id
0.4s    Worker       Update status: "running"
0.5s    Worker       Convert to 16kHz mono WAV
2.0s    Worker       Start VAD segmentation
2.5s    Worker       Segment 0 detected → ASR
3.7s    Worker       Segment 0 → Translate
3.9s    Worker       Segment 0 → TTS
4.7s    Worker       Segment 0 → Publish event
        Client       Receives segment 0 event via SSE
8.0s    Worker       Segment 1 detected → Process
12.0s   Worker       Segment 1 → Publish event
        Client       Receives segment 1 event via SSE
15.0s   Worker       Segment 2 detected → Process
19.0s   Worker       Segment 2 → Publish event
        Client       Receives segment 2 event via SSE
20.0s   Worker       Concatenate all segments
21.0s   Worker       Update status: "done"
21.0s   Client       Receives "done" event via SSE
21.5s   Client       GET /v1/dubs/{id}/result
21.6s   Client       Downloads final.wav
```

## Key Design Decisions

### Why Redis?
- **Persistence**: State survives restarts
- **Pub/Sub**: Built-in event broadcasting
- **Queue**: Simple BLPOP for job distribution
- **Scalability**: Works across multiple instances

### Why Separate Worker?
- **Responsiveness**: FastAPI never blocks
- **Scalability**: Can run multiple workers
- **Isolation**: Worker crashes don't affect API
- **Resource Management**: CPU-intensive work separate from I/O

### Why Streaming VAD?
- **User Experience**: See progress immediately
- **Lower Latency**: First segment appears faster
- **Better Feedback**: Real-time progress updates

### Why SSE (Server-Sent Events)?
- **Simple**: One-way stream from server to client
- **Real-time**: Events appear as they happen
- **Standard**: Works in browsers, curl, etc.
- **Efficient**: Less overhead than WebSockets for this use case
