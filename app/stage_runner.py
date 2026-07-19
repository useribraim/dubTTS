"""
Per-stage runners for the streaming pipeline.

Each runner consumes segment tasks from its stage stream with a consumer
group, executes the stage (ASR / MT / TTS), enqueues the task to the next
stage, and acks. Failures are re-enqueued with an incremented attempt
counter (bounded retries); tasks that exhaust attempts are counted as
failed (dead-lettering lands in Phase 3).

Run standalone:
    python -m app.stage_runner --stage asr
    python -m app.stage_runner --stage mt
    python -m app.stage_runner --stage tts
"""

import argparse
import asyncio
import os
import socket
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

import redis.asyncio as redis
from redis.exceptions import ResponseError

from app.logging_config import setup_logging, get_logger
from app.metrics import (
    increment_failure,
    increment_retry,
    record_job_timing,
    record_latency,
)
from app.redis_backend import REDIS_URL, RedisEventBus, RedisJobStore
from app.streaming import (
    SEG_GROUPS,
    SEG_MAX_ATTEMPTS,
    SEG_STREAM_MAXLEN,
    SEG_STREAMS,
    StreamSessionStore,
    decode_envelope,
)

setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), json_output=os.getenv("LOG_JSON", "0") == "1")
logger = get_logger(__name__)

STREAMS_ROOT = os.getenv("STREAMS_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "streams"))


def _consumer_name(stage: str) -> str:
    explicit = os.getenv("WORKER_NAME")
    if explicit:
        return explicit
    return f"{stage}-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def done_set_key(session_id: str) -> str:
    return f"dub:stream:{session_id}:done_set"


def tts_out_path(job_id: str, idx: int) -> str:
    return os.path.join(STREAMS_ROOT, job_id, "segments_out", f"dub_{idx:04d}.wav")


async def is_segment_done(ctx: "StageContext", job_id: str, idx: int, tts_path: str) -> bool:
    """True if the segment's audio was already produced and recorded."""
    return bool(
        await ctx.r.sismember(done_set_key(job_id), idx)
        and await ctx.store.get_segment_meta(job_id, idx) is not None
        and os.path.exists(tts_path)
    )


async def _ensure_group(r: redis.Redis, stream: str, group: str) -> None:
    try:
        await r.xgroup_create(stream, group, id="0-0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def maybe_complete_session(r: redis.Redis, session_id: str, bus: RedisEventBus) -> bool:
    """
    Stitch the final WAV once the session is finalized and every segment
    produced audio. Idempotent: guarded by a short-lived completion lock.
    """
    from app.pipeline_redis import _concat_wavs_ffmpeg

    session_store = StreamSessionStore(r)
    try:
        session = await session_store.get_session(session_id)
    except KeyError:
        return False

    finalized = session.get("finalized") == "1"
    total = int(session.get("total_segments", "0"))
    done = int(await r.scard(done_set_key(session_id)))

    if not finalized:
        return False
    if total == 0:
        if await session_store.acquire_completion_lock(session_id):
            await session_store.update_session(session_id, status="failed", error="no_speech_detected")
            await bus.publish(session_id, "status", {"status": "failed", "error": "no_speech_detected"})
        return False
    if done < total:
        return False
    if not await session_store.acquire_completion_lock(session_id):
        return False

    store = RedisJobStore(r)
    segment_paths = await store.list_segments(session_id)
    final_path = os.path.join(STREAMS_ROOT, session_id, "final.wav")
    await asyncio.to_thread(_concat_wavs_ffmpeg, segment_paths, final_path)

    created_at = float(session.get("created_at") or time.time())
    end_to_end_ms = int((time.time() - created_at) * 1000)
    try:
        await record_job_timing(session_id, "stream_end_to_end", end_to_end_ms)
    except Exception:
        pass

    await session_store.update_session(session_id, status="done", output_path=final_path)
    await bus.publish(session_id, "done", {"output_path": final_path})
    await bus.publish(session_id, "status", {"status": "done"})
    logger.info(
        "Streaming session completed",
        extra={"job_id": session_id, "segments": total, "end_to_end_ms": end_to_end_ms, "operation": "session_complete"},
    )
    return True


async def _handle_asr(ctx: "StageContext", env: Dict[str, str]) -> Dict[str, str]:
    from app.asr import transcribe

    if env.get("segment_index") == "0" and env.get("attempts") == "0":
        await ctx.session_store.update_session(env["job_id"], status="running")
        await ctx.bus.publish(env["job_id"], "status", {"status": "running"})

    t0 = time.time()
    model_size = os.getenv("ASR_MODEL_SIZE", "small")
    src_text = await asyncio.to_thread(transcribe, env["audio_path"], env.get("src_lang"), model_size)
    asr_ms = int((time.time() - t0) * 1000)
    try:
        await record_latency("asr", asr_ms)
    except Exception:
        pass
    logger.info(
        "ASR stage done",
        extra={
            "job_id": env["job_id"],
            "segment_id": env["segment_id"],
            "duration_ms": asr_ms,
            "text_preview": src_text[:60],
            "operation": "stage_asr",
        },
    )
    return {"src_text": src_text, "asr_ms": str(asr_ms)}


async def _handle_mt(ctx: "StageContext", env: Dict[str, str]) -> Dict[str, str]:
    from app.aws_nlp import translate_text_async

    t0 = time.time()
    tgt_text = await translate_text_async(env.get("src_text", ""), env.get("src_lang", "en"), env.get("tgt_lang", "ru"))
    mt_ms = int((time.time() - t0) * 1000)
    # translate_text_async records its own latency/cache metrics.
    logger.info(
        "MT stage done",
        extra={"job_id": env["job_id"], "segment_id": env["segment_id"], "duration_ms": mt_ms, "operation": "stage_mt"},
    )
    return {"tgt_text": tgt_text, "mt_ms": str(mt_ms)}


async def finalize_tts_segment(ctx: "StageContext", env: Dict[str, str], tts_path: str, tts_ms: int) -> None:
    """
    Bookkeeping once segment audio exists at tts_path: idempotent state
    updates, replayable events, and session-completion check. Shared by the
    in-process stage runner and the gRPC dispatcher.
    """
    job_id = env["job_id"]
    idx = int(env["segment_index"])

    if await is_segment_done(ctx, job_id, idx, tts_path):
        existing_meta = await ctx.store.get_segment_meta(job_id, idx)
        await ctx.bus.publish(job_id, "segment", existing_meta)
        logger.info(
            "Segment already synthesized, replayed from state",
            extra={"job_id": job_id, "segment_id": env["segment_id"], "operation": "stage_tts_replay"},
        )
        return

    await ctx.store.append_segment(job_id, tts_path, idx)
    await ctx.r.sadd(done_set_key(job_id), idx)
    done_count = int(await ctx.r.scard(done_set_key(job_id)))
    await ctx.session_store.update_session(job_id, done_segments=done_count)

    now = time.time()
    onset_to_audio_ms = int((now - float(env.get("onset_ts", now))) * 1000)
    try:
        await record_latency("segment_onset_to_audio", onset_to_audio_ms)
    except Exception:
        pass
    if done_count == 1:
        try:
            await record_job_timing(job_id, "time_to_first_audio", onset_to_audio_ms)
        except Exception:
            pass
        await ctx.session_store.update_session(job_id, first_audio_ts=f"{now:.3f}")

    meta = {
        "segment_index": idx,
        "segment_id": env["segment_id"],
        "job_id": job_id,
        "src_text": env.get("src_text", ""),
        "tgt_text": env.get("tgt_text", ""),
        "audio_path": tts_path,
        "asr_ms": int(env.get("asr_ms", "0") or 0),
        "mt_ms": int(env.get("mt_ms", "0") or 0),
        "tts_ms": tts_ms,
        "onset_to_audio_ms": onset_to_audio_ms,
    }
    await ctx.store.set_segment_meta(job_id, idx, meta)
    await ctx.bus.publish(job_id, "segment", meta)
    logger.info(
        "TTS stage done",
        extra={
            "job_id": job_id,
            "segment_id": env["segment_id"],
            "duration_ms": tts_ms,
            "onset_to_audio_ms": onset_to_audio_ms,
            "done_segments": done_count,
            "operation": "stage_tts",
        },
    )

    await maybe_complete_session(ctx.r, job_id, ctx.bus)


async def _handle_tts(ctx: "StageContext", env: Dict[str, str]) -> Dict[str, str]:
    job_id = env["job_id"]
    idx = int(env["segment_index"])
    tts_path = tts_out_path(job_id, idx)

    if await is_segment_done(ctx, job_id, idx, tts_path):
        # Duplicate delivery of a completed segment: replay event, no re-synthesis.
        existing_meta = await ctx.store.get_segment_meta(job_id, idx)
        await ctx.bus.publish(job_id, "segment", existing_meta)
        logger.info(
            "Segment already synthesized, replayed from state",
            extra={"job_id": job_id, "segment_id": env["segment_id"], "operation": "stage_tts_replay"},
        )
        return {}

    t0 = time.time()
    tts_provider = os.getenv("TTS_PROVIDER", "aws")
    os.makedirs(os.path.dirname(tts_path), exist_ok=True)
    try:
        from app.tts_providers import tts_with_provider

        await asyncio.to_thread(tts_with_provider, env.get("tgt_text", ""), env.get("voice", "Tatyana"), tts_path, "16000", tts_provider)
    except ImportError:
        from app.aws_nlp import tts_to_wav

        await asyncio.to_thread(tts_to_wav, env.get("tgt_text", ""), env.get("voice", "Tatyana"), tts_path)
    tts_ms = int((time.time() - t0) * 1000)
    try:
        await record_latency("tts", tts_ms)
    except Exception:
        pass

    await finalize_tts_segment(ctx, env, tts_path, tts_ms)
    return {}


class StageContext:
    def __init__(self, r: redis.Redis, stage: str):
        self.r = r
        self.stage = stage
        self.store = RedisJobStore(r)
        self.session_store = StreamSessionStore(r)
        self.bus = RedisEventBus(r)


STAGE_HANDLERS: Dict[str, Callable[[StageContext, Dict[str, str]], Awaitable[Dict[str, str]]]] = {
    "asr": _handle_asr,
    "mt": _handle_mt,
    "tts": _handle_tts,
}
NEXT_STAGE = {"asr": "mt", "mt": "tts", "tts": None}


async def run_stage_loop(
    stage: str,
    handler: Callable[["StageContext", Dict[str, str]], Awaitable[Dict[str, str]]],
    stop_event: Optional[asyncio.Event] = None,
    ctx: Optional["StageContext"] = None,
    consumer: Optional[str] = None,
) -> None:
    """
    Core claim -> execute -> enqueue-downstream -> ack loop for one stage.
    Shared by the in-process stage runner and the gRPC dispatcher.
    """
    r = redis.from_url(REDIS_URL, decode_responses=False)
    if ctx is None:
        ctx = StageContext(r, stage)
    else:
        r = ctx.r
    stream = SEG_STREAMS[stage]
    group = SEG_GROUPS[stage]
    consumer = consumer or _consumer_name(stage)
    await _ensure_group(r, stream, group)

    logger.info(
        "Stage runner started",
        extra={"stage": stage, "stream": stream, "group": group, "consumer": consumer, "operation": "stage_runner_start"},
    )

    while stop_event is None or not stop_event.is_set():
        try:
            messages = await r.xreadgroup(group, consumer, streams={stream: ">"}, count=1, block=2000)
        except Exception as e:
            logger.error("Stage read failed", extra={"stage": stage, "error": str(e)}, exc_info=True)
            await asyncio.sleep(1)
            continue
        if not messages:
            continue

        _s, entries = messages[0]
        entry_id, fields = entries[0]
        env = decode_envelope(fields)
        segment_id = env.get("segment_id", "?")
        attempts = int(env.get("attempts", "0") or 0)

        try:
            enqueue_ts = float(env.get("enqueue_ts", "0") or 0)
            if enqueue_ts:
                await record_latency(f"queue_delay_{stage}", (time.time() - enqueue_ts) * 1000)
        except Exception:
            pass

        logger.info(
            "Stage task claimed",
            extra={"stage": stage, "segment_id": segment_id, "attempts": attempts, "operation": "stage_claim"},
        )

        try:
            updates = await handler(ctx, env)
        except Exception as e:
            logger.error(
                "Stage execution failed",
                extra={"stage": stage, "segment_id": segment_id, "attempts": attempts, "error": str(e)},
                exc_info=True,
            )
            if attempts + 1 >= SEG_MAX_ATTEMPTS:
                increment_failure()
                await ctx.bus.publish(
                    env.get("job_id", ""),
                    "segment_failed",
                    {"segment_id": segment_id, "stage": stage, "error": str(e), "attempts": attempts + 1},
                )
                await r.xack(stream, group, entry_id)
            else:
                increment_retry()
                env["attempts"] = str(attempts + 1)
                env["enqueue_ts"] = f"{time.time():.3f}"
                await r.xadd(stream, env, maxlen=SEG_STREAM_MAXLEN, approximate=True)
                await r.xack(stream, group, entry_id)
            continue

        next_stage = NEXT_STAGE[stage]
        if next_stage is not None:
            env.update(updates)
            env["attempts"] = "0"
            env["enqueue_ts"] = f"{time.time():.3f}"
            await r.xadd(SEG_STREAMS[next_stage], env, maxlen=SEG_STREAM_MAXLEN, approximate=True)
        await r.xack(stream, group, entry_id)


async def run_stage(stage: str, stop_event: Optional[asyncio.Event] = None) -> None:
    if stage not in STAGE_HANDLERS:
        raise ValueError(f"unknown stage: {stage}")
    await run_stage_loop(stage, STAGE_HANDLERS[stage], stop_event=stop_event)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a streaming stage worker")
    parser.add_argument("--stage", required=True, choices=sorted(STAGE_HANDLERS.keys()))
    args = parser.parse_args()
    asyncio.run(run_stage(args.stage))


if __name__ == "__main__":
    main()
