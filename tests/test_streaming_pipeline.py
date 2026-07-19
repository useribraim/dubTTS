"""
End-to-end tests for the streaming pipeline:
WebSocket ingest -> segmenter -> ASR -> MT -> TTS stage streams -> stitched result.

Runs the segmenter and stage runners in a background thread with its own
event loop so the sync TestClient can drive the API.
"""

import asyncio
import json
import math
import os
import struct
import threading
import time

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient

os.environ.setdefault("ASR_MODEL_SIZE", "tiny")
os.environ.setdefault("STREAM_SEGMENT_MS", "5000")


def _make_pcm(seconds: float, freq: float = 440.0, rate: int = 16000) -> bytes:
    """Generate a sine wave as 16 kHz s16le mono PCM."""
    frames = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(frames)
    )


@pytest.fixture(scope="module")
def client():
    from app.main import app

    # Context-managed TestClient keeps one portal loop for all requests and
    # websocket sessions, matching the app's shared Redis connection pool.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def pipeline():
    """Start segmenter + stage runners in a background thread; flush test DB."""
    from app.segmenter import run_segmenter
    from app.stage_runner import run_stage

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/1")
    rc = sync_redis.Redis.from_url(redis_url)
    try:
        rc.ping()
    except Exception as e:
        pytest.skip(f"Redis not available: {e}")
    rc.flushdb()

    holder = {}
    started = threading.Event()

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        holder["loop"] = loop
        holder["tasks"] = [loop.create_task(run_segmenter())] + [
            loop.create_task(run_stage(stage)) for stage in ("asr", "mt", "tts")
        ]
        started.set()
        try:
            loop.run_forever()
        finally:
            for task in holder["tasks"]:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*holder["tasks"], return_exceptions=True))
            loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    time.sleep(1.0)  # let consumer groups initialize
    yield holder
    holder["loop"].call_soon_threadsafe(holder["loop"].stop)
    thread.join(timeout=10)


def _wait_for_terminal_status(client: TestClient, session_id: str, timeout_s: float = 120.0) -> dict:
    deadline = time.time() + timeout_s
    status = None
    while time.time() < deadline:
        resp = client.get(f"/v1/streams/{session_id}")
        assert resp.status_code == 200
        status = resp.json()
        if status["status"] in ("done", "failed"):
            return status
        time.sleep(1.0)
    raise AssertionError(f"session {session_id} did not reach a terminal state; last={status}")


def test_envelope_roundtrip():
    from app.streaming import decode_envelope, make_envelope

    env = make_envelope(
        session_id="sess1",
        segment_index=3,
        audio_path="/tmp/seg.wav",
        src_lang="en",
        tgt_lang="ru",
        voice="Tatyana",
        onset_ts=1700000000.0,
    )
    assert env["segment_id"] == "sess1:3"
    assert env["job_id"] == "sess1"
    raw = {k.encode(): v.encode() for k, v in env.items()}
    decoded = decode_envelope(raw)
    assert decoded == env


def test_streaming_session_end_to_end(client: TestClient, pipeline):
    resp = client.post(
        "/v1/streams",
        data={"src_lang": "en", "tgt_lang": "ru", "voice": "Tatyana"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    pcm = _make_pcm(12.0)  # 2 full 5 s segments + 2 s tail
    with client.websocket_connect(f"/v1/streams/{session_id}/audio") as ws:
        chunk = 8000  # 250 ms of audio
        for offset in range(0, len(pcm), chunk):
            ws.send_bytes(pcm[offset : offset + chunk])
        ws.send_json({"type": "finalize"})
        assert ws.receive_json() == {"type": "finalized"}

    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done", f"session failed: {status}"
    assert status["total_segments"] == 3
    assert status["done_segments"] == status["total_segments"]

    seg = client.get(f"/v1/streams/{session_id}/segments/0")
    assert seg.status_code == 200
    assert len(seg.content) > 44  # WAV header + payload

    res = client.get(f"/v1/streams/{session_id}/result")
    assert res.status_code == 200
    assert len(res.content) > 44

    # Replayable events + traceable IDs, straight from the Redis event stream.
    rc = sync_redis.Redis.from_url(os.environ["REDIS_URL"])
    events = rc.xrange(f"dub:job:{session_id}:events_stream")
    seg_events = [e for e in events if (e[1].get(b"type") or b"").decode() == "segment"]
    assert len(seg_events) == status["total_segments"]
    first = json.loads(seg_events[0][1][b"data"].decode())
    assert first["segment_id"] == f"{session_id}:0"
    assert first["job_id"] == session_id
    assert "onset_to_audio_ms" in first


def test_streaming_session_no_audio_fails_cleanly(client: TestClient, pipeline):
    resp = client.post("/v1/streams", data={})
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    with client.websocket_connect(f"/v1/streams/{session_id}/audio") as ws:
        ws.send_json({"type": "finalize"})
        assert ws.receive_json() == {"type": "finalized"}

    status = _wait_for_terminal_status(client, session_id, timeout_s=30.0)
    assert status["status"] == "failed"
    assert status["error"] == "no_speech_detected"


def test_streaming_session_status_not_found(client: TestClient):
    resp = client.get("/v1/streams/does-not-exist")
    assert resp.status_code == 404
