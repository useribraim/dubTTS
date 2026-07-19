# PLAN — Streaming Voice-Over Translation Pipeline

Roadmap to evolve this repository from a batch dubbing MVP into a low-latency
streaming voice-over translation system: chunked audio in, gRPC stage workers
coordinated through Redis Streams, translated audio out while source speech
continues.

Each phase is one committed milestone. `pytest` must be green before every
commit. Numeric targets are validated by measurement in Phase 5; published
docs carry the actually measured values.

## Decisions

- Streaming ingest: **WebSocket** (binary 16 kHz s16le mono PCM frames + JSON control messages)
- gRPC coordination: **dispatcher pattern** — Redis Streams are the task queues;
  a dispatcher per stage claims tasks and calls gRPC worker replicas
- Validation: **local mode `USE_AWS=0`** by default, with a documented real-AWS
  reproduction path
- The existing batch pipeline (`POST /v1/dubs`) **stays** as a regression baseline

## Phase 0 — Baseline (done)

- Commit pending working-tree changes; add this plan.

## Phase 1 — WebSocket streaming ingest + segmented stage-stream pipeline

- Redis stage streams with consumer groups: `dub:seg:asr`, `dub:seg:mt`, `dub:seg:tts`
- Task envelope: `job_id`, `segment_index`, `segment_id` (`{job_id}:{idx}`),
  `audio_path`, langs, voice, `onset_ts`, per-stage `enqueue_ts`
- `POST /v1/streams` creates a session; `WS /v1/streams/{id}/audio` ingests PCM
  chunks; progress over the existing replayable SSE events
- `app/segmenter.py`: cuts incoming audio into ~5 s segments while speech
  continues, enqueues to `dub:seg:asr`
- `app/stage_runner.py`: per-stage async runners (ASR → MT → TTS) over the
  stage streams; session completion tracking + stitched result
- `tests/streaming_client.py`: file-driven WS client (real-time and burst modes)
- Tests: end-to-end segment flow, event replay, ID traceability

## Phase 2 — gRPC workers + dispatchers

- `proto/dubbing.proto`: TranscriptionService, TranslationService,
  SynthesisService, Health; generated code in `app/pb/`
- gRPC servers wrapping existing `asr.py` / `aws_nlp.py` / `tts_providers.py`
- `app/dispatcher.py`: XREADGROUP/XAUTOCLAIM → gRPC call (timeout, round-robin
  pool from env) → write downstream → XACK
- `grpcio`, `grpcio-tools` added to requirements
- Tests: dispatcher ↔ in-process gRPC round trip, three-stage integration

## Phase 3 — Bounded retries, dead-letter, crash recovery

- Per-task attempt counter; retry ≤ 2 with backoff; then `dub:seg:dlq` with
  stage, error, attempts
- XAUTOCLAIM of stale pending entries on restart; segment-meta cache skips
  completed stages (zero re-transcription after worker crash)
- Metrics: `dub_stage_retries_total{stage,outcome}`, `dub_dlq_total{stage}`
- Tests: injected failures (recover, then DLQ); kill-worker-mid-session test

## Phase 4 — Metrics & Grafana

- `dub_stage_queue_delay_ms{stage}` (claim_ts − enqueue_ts) + queue-delay share
  of end-to-end in `/v1/metrics`
- `dub_stage_backlog{stage}` gauge (pending + lag per stream)
- `dub_time_to_first_audio_ms` histogram (segment-0 onset → first translated audio)
- Prometheus scrape configs for new ports; Grafana dashboard v2

## Phase 5 — Docker packaging + 8-stream validation

- Multi-stage `Dockerfile`; commands: api, segmenter, dispatcher-{asr,mt,tts},
  worker-{asr,mt,tts}
- `docker-compose.full.yml`: redis + api + segmenter + dispatchers + worker
  replicas, `cpus` limits totaling 4 cores, no GPU
- `tests/load_streaming.py`: 8 concurrent WS sessions streaming 5 s chunks;
  measures per-segment onset→audio p50, TTFA, backlog, retries
- 500+ segment soak run for recovery/DLQ rates; results in PERFORMANCE_RESULTS.md

## Phase 6 — Docs alignment

- README: new architecture, WS API, gRPC services, Docker quickstart
- CLAIM_ALIGNMENT.md: resume-safe wording matched to measured results
- ARCHITECTURE_FLOW.md, OBSERVABILITY.md, PERFORMANCE_METRICS.md updates

## Claim alignment

| Target claim | Covered by |
| --- | --- |
| Chunked pipeline, synthesizing while speech continues | Phase 1 |
| gRPC workers coordinated through Redis Streams | Phase 2 |
| Traceable job and segment IDs | Phase 1 (envelope + events) |
| 8 concurrent streams, single 4-core Docker host, no GPU | Phase 5 |
| ~3.2 s p50 onset→audio per 5 s segment | Phases 1 + 5 (measured) |
| Replayable events, Redis state, translation caching | Existing repo features |
| Bounded retries, ~97% recovery ≤ 2 retries, DLQ < 1% (500+ segments) | Phases 3 + 5 (measured) |
| Session recovery, zero re-transcription | Phase 3 (tested) |
| TTFA, per-stage p50, queue delay < 8%, retry outcomes, backlog | Phase 4 |
