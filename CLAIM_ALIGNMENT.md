# Claim Alignment Notes

Use this file as the repo-side explanation for the submitted resume entry. It keeps the interview story tied to code that is present in this repository.

## Resume Project

**Voice-Over Translation System - ML Inference Testing & Service Pipeline**

## What This Repository Demonstrates

- **Python services and REST APIs:** FastAPI routes in `app/main.py` create dubbing jobs, expose status/result endpoints, and stream progress events.
- **Containerised service workflow:** Redis, Prometheus, and Grafana can run through Docker Compose files. The API and worker are separate processes.
- **ML inference workflow:** `app/asr.py` wraps Faster-Whisper transcription; `app/pipeline_redis.py` runs ASR -> translation -> TTS per speech segment.
- **Translation and TTS pipeline:** `app/aws_nlp.py` integrates AWS Translate and AWS Polly, with `USE_AWS=0` for local pipeline validation without credentials.
- **Repeatable test inputs and regression checks:** `tests/` covers API behavior, Redis-backed job state, caching, and performance/reporting paths.
- **Structured logging and observability:** `app/logging_config.py`, `app/metrics.py`, `OBSERVABILITY.md`, and the Grafana dashboard provide runtime diagnostics.

## Claims To Phrase Carefully

- Say **Redis-backed job state, queueing, caching, metrics, and event replay** for this repo. Do not describe this version as PostgreSQL-backed unless discussing an earlier/private variant outside the repository.
- Say **Faster-Whisper/CT2-based ASR inference plus optional local F5-TTS provider** for this repo. Do not claim a custom PyTorch training implementation here.
- Say **Docker-supported local services** rather than a fully packaged production deployment. This repo has Docker Compose support for Redis and observability, while the API/worker run locally from Python.

## Interview-Safe Summary

"This repository is my local, testable version of the voice-over translation pipeline. It uses FastAPI for REST endpoints, Redis Streams for asynchronous job processing and replayable progress events, Faster-Whisper for ASR, AWS Translate and Polly for cloud translation/TTS, and pytest plus metrics endpoints for regression and performance checks. The submitted resume groups it under ML inference testing and service pipelines; in this public repo, Redis is the backing store rather than PostgreSQL."
