"""
Reliability tests for the streaming pipeline:
- bounded retries with recovery
- dead-lettering after attempts are exhausted
- stale pending-entry reclaim (worker crash recovery)
- zero re-transcription of completed segments
"""

import asyncio
import io
import math
import os
import struct
import time
import wave

import pytest
import redis as sync_redis

import app.stage_runner as stage_runner
from app.stage_runner import run_stage_loop, _handle_asr
from app.streaming import (
    DLQ_STREAM_KEY,
    SEG_GROUPS,
    SEG_MAX_ATTEMPTS,
    SEG_STREAMS,
    make_envelope,
)

os.environ.setdefault("ASR_MODEL_SIZE", "tiny")


@pytest.fixture(autouse=True)
def flush_test_db():
    rc = sync_redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/1"))
    try:
        rc.ping()
    except Exception as e:
        pytest.skip(f"Redis not available: {e}")
    rc.flushdb()
    yield rc
    rc.flushdb()


def _envelope(session_id: str, idx: int, audio_path: str = "/tmp/seg.wav") -> dict:
    return make_envelope(
        session_id=session_id,
        segment_index=idx,
        audio_path=audio_path,
        src_lang="en",
        tgt_lang="ru",
        voice="Tatyana",
        onset_ts=time.time(),
    )


async def _wait_for(condition, timeout_s: float = 15.0, interval: float = 0.2) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if condition():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
async def test_retry_then_recover(flush_test_db):
    """A task that fails once is retried and recovered (counters reflect it)."""
    rc = flush_test_db
    rc.xadd(SEG_STREAMS["asr"], _envelope("sess-retry", 0))

    calls = {"n": 0}

    async def flaky_handler(ctx, env):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient boom")
        return {"src_text": "recovered text"}

    task = asyncio.create_task(run_stage_loop("asr", flaky_handler))
    try:
        ok = await _wait_for(lambda: rc.xlen(SEG_STREAMS["mt"]) == 1)
        assert ok, "task was not forwarded downstream after recovery"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    downstream = rc.xrange(SEG_STREAMS["mt"])
    assert len(downstream) == 1
    fields = {k.decode(): v.decode() for k, v in downstream[0][1].items()}
    assert fields["src_text"] == "recovered text"
    assert fields["attempts"] == "0"  # reset for the next stage
    assert fields["segment_id"] == "sess-retry:0"

    assert int(rc.get("dub:metrics:retry:asr") or 0) == 1
    assert int(rc.get("dub:metrics:recovered:asr") or 0) == 1
    assert int(rc.get("dub:metrics:success:asr") or 0) == 1
    assert rc.xlen(DLQ_STREAM_KEY) == 0


@pytest.mark.asyncio
async def test_dead_letter_after_max_attempts(flush_test_db):
    """A task that always fails is dead-lettered after SEG_MAX_ATTEMPTS."""
    rc = flush_test_db
    rc.xadd(SEG_STREAMS["asr"], _envelope("sess-dlq", 0))

    async def always_fail(ctx, env):
        raise RuntimeError("permanent boom")

    task = asyncio.create_task(run_stage_loop("asr", always_fail))
    try:
        ok = await _wait_for(lambda: rc.xlen(DLQ_STREAM_KEY) == 1)
        assert ok, "task was not dead-lettered"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    dlq = rc.xrange(DLQ_STREAM_KEY)
    assert len(dlq) == 1
    fields = {k.decode(): v.decode() for k, v in dlq[0][1].items()}
    assert fields["stage"] == "asr"
    assert fields["segment_id"] == "sess-dlq:0"
    assert fields["attempts"] == str(SEG_MAX_ATTEMPTS - 1)
    assert "permanent boom" in fields["error"]
    assert "failed_at" in fields

    # Never forwarded downstream; retries bounded.
    assert rc.xlen(SEG_STREAMS["mt"]) == 0
    assert int(rc.get("dub:metrics:dlq:asr") or 0) == 1
    assert int(rc.get("dub:metrics:retry:asr") or 0) == SEG_MAX_ATTEMPTS - 1


def _wav_bytes(seconds: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000)))
            for i in range(int(16000 * seconds))
        )
        wf.writeframes(frames)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_stale_pending_reclaimed_and_completed_segment_skipped(flush_test_db, tmp_path, monkeypatch):
    """
    A crashed consumer's pending entries are reclaimed; a segment that already
    completed the pipeline is not re-transcribed.
    """
    rc = flush_test_db
    monkeypatch.setattr(stage_runner, "CLAIM_IDLE_MS", 300)
    monkeypatch.setattr(stage_runner, "STREAMS_ROOT", str(tmp_path))

    session_id = "sess-crash"

    # Segment 0 already fully completed: meta + done-set + synthesized file.
    done0 = tmp_path / session_id / "segments_out" / "dub_0000.wav"
    done0.parent.mkdir(parents=True, exist_ok=True)
    done0.write_bytes(_wav_bytes(0.5))

    import redis.asyncio as aioredis
    from app.redis_backend import RedisJobStore

    ar = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=False)
    store = RedisJobStore(ar)
    await store.set_segment_meta(session_id, 0, {"segment_id": f"{session_id}:0", "audio_path": str(done0)})
    await ar.sadd(stage_runner.done_set_key(session_id), 0)

    # Segment 1 pending as a real ASR task (needs a real WAV on disk).
    seg1_wav = tmp_path / "seg1_in.wav"
    seg1_wav.write_bytes(_wav_bytes(1.0))

    group = SEG_GROUPS["asr"]
    try:
        await ar.xgroup_create(SEG_STREAMS["asr"], group, id="0-0", mkstream=True)
    except Exception:
        pass
    await ar.xadd(SEG_STREAMS["asr"], _envelope(session_id, 0, str(done0)))
    await ar.xadd(SEG_STREAMS["asr"], _envelope(session_id, 1, str(seg1_wav)))

    # Fake crashed worker: claims both entries, never processes or acks them.
    claimed = await ar.xreadgroup(group, "crashed-worker", streams={SEG_STREAMS["asr"]: ">"}, count=2)
    assert claimed and len(claimed[0][1]) == 2

    spy_calls = []

    async def spy_handler(ctx, env):
        spy_calls.append(env["segment_id"])
        return await _handle_asr(ctx, env)

    task = asyncio.create_task(run_stage_loop("asr", spy_handler))
    try:
        ok = await _wait_for(lambda: rc.xlen(SEG_STREAMS["mt"]) >= 1, timeout_s=60.0)
        assert ok, "segment 1 was not reclaimed and processed"
        # Wait for the completed segment's entry to be acked as well.
        ok = await _wait_for(lambda: rc.xpending(SEG_STREAMS["asr"], group)["pending"] == 0, timeout_s=30.0)
        assert ok, "pending entries were not all acked"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    # Zero re-transcription: the completed segment never reached the handler.
    assert spy_calls == [f"{session_id}:1"]

    downstream = rc.xrange(SEG_STREAMS["mt"])
    assert len(downstream) == 1
    fields = {k.decode(): v.decode() for k, v in downstream[0][1].items()}
    assert fields["segment_id"] == f"{session_id}:1"
    await ar.aclose()
