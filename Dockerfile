# Multi-stage build: one image, multiple entrypoints selected via `command:`
# in docker-compose.full.yml (api, segmenter, dispatcher-{asr,mt,tts},
# worker-{asr,mt,tts}). CPU-only — no GPU base image, no F5-TTS weights baked in.

FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY proto ./proto

RUN mkdir -p /srv/data/uploads /srv/data/outputs /srv/data/streams

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv \
    USE_AWS=0

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
