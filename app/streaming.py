"""
Streaming pipeline primitives.

Defines the Redis schema for the streaming voice-over pipeline:
session state, per-session chunk streams, and the per-stage task
streams (ASR -> MT -> TTS) with traceable job/segment envelopes.
"""

import time
from typing import Any, Dict, Optional

import redis.asyncio as redis

# Session registry stream: one entry per created streaming session.
# Segmenter instances consume this with a consumer group to pick up sessions.
SESSIONS_STREAM_KEY = "dub:streams:sessions"
SESSIONS_GROUP = "dub:streams:segmenters"

# Per-stage task streams and their consumer groups.
SEG_STREAMS = {
    "asr": "dub:seg:asr",
    "mt": "dub:seg:mt",
    "tts": "dub:seg:tts",
}
SEG_GROUPS = {
    "asr": "dub:seg:asr:workers",
    "mt": "dub:seg:mt:workers",
    "tts": "dub:seg:tts:workers",
}

# Dead-letter stream for segment tasks that exhaust retries (Phase 3).
DLQ_STREAM_KEY = "dub:seg:dlq"

# Audio format expected on the WebSocket: 16 kHz s16le mono PCM.
SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit samples

# Default segment window and caps.
SEGMENT_MS = int(__import__("os").getenv("STREAM_SEGMENT_MS", "5000"))
CHUNK_STREAM_MAXLEN = int(__import__("os").getenv("CHUNK_STREAM_MAXLEN", "20000"))
SEG_STREAM_MAXLEN = int(__import__("os").getenv("SEG_STREAM_MAXLEN", "100000"))

# Bounded retries: initial execution + 2 retries by default.
SEG_MAX_ATTEMPTS = int(__import__("os").getenv("SEG_MAX_ATTEMPTS", "3"))


def _now_ts() -> float:
    return time.time()


def session_key(session_id: str) -> str:
    return f"dub:stream:{session_id}"


def chunks_stream_key(session_id: str) -> str:
    return f"dub:stream:{session_id}:chunks"


def completing_key(session_id: str) -> str:
    return f"dub:stream:{session_id}:completing"


def segment_id_for(session_id: str, segment_index: int) -> str:
    """Traceable segment ID used in envelopes, logs, state, and events."""
    return f"{session_id}:{segment_index}"


def make_envelope(
    session_id: str,
    segment_index: int,
    audio_path: str,
    src_lang: str,
    tgt_lang: str,
    voice: str,
    onset_ts: float,
    attempts: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Build a stage-task envelope (all values stringified for XADD)."""
    env: Dict[str, Any] = {
        "job_id": session_id,
        "segment_index": segment_index,
        "segment_id": segment_id_for(session_id, segment_index),
        "audio_path": audio_path,
        "src_lang": src_lang,
        "tgt_lang": tgt_lang,
        "voice": voice,
        "onset_ts": f"{onset_ts:.3f}",
        "enqueue_ts": f"{_now_ts():.3f}",
        "attempts": attempts,
    }
    if extra:
        env.update(extra)
    return {k: str(v) for k, v in env.items()}


def decode_envelope(fields: Dict[bytes, bytes]) -> Dict[str, str]:
    """Decode a raw Redis stream entry into a string envelope."""
    return {
        (k.decode() if isinstance(k, (bytes, bytearray)) else k):
        (v.decode() if isinstance(v, (bytes, bytearray)) else v)
        for k, v in fields.items()
    }


class StreamSessionStore:
    """Redis-backed state for a streaming session."""

    def __init__(self, r: redis.Redis):
        self.r = r

    async def create_session(self, session_id: str, fields: Dict[str, Any]) -> None:
        base = {
            "session_id": session_id,
            "status": "open",
            "src_lang": "en",
            "tgt_lang": "ru",
            "voice": "Tatyana",
            "created_at": f"{_now_ts():.3f}",
            "updated_at": f"{_now_ts():.3f}",
            "finalized": "0",
            "total_segments": "0",
            "done_segments": "0",
            "output_path": "",
            "error": "",
            "first_audio_ts": "",
        }
        base.update({k: str(v) for k, v in fields.items()})
        await self.r.hset(session_key(session_id), mapping=base)
        await self.r.xadd(SESSIONS_STREAM_KEY, {"session_id": session_id})

    async def get_session(self, session_id: str) -> Dict[str, str]:
        data = await self.r.hgetall(session_key(session_id))
        if not data:
            raise KeyError(session_id)
        return {k.decode(): v.decode() for k, v in data.items()}

    async def update_session(self, session_id: str, **kwargs: Any) -> None:
        mapping = {k: str(v) for k, v in kwargs.items()}
        mapping["updated_at"] = f"{_now_ts():.3f}"
        await self.r.hset(session_key(session_id), mapping=mapping)

    async def increment_total_segments(self, session_id: str) -> int:
        return int(await self.r.hincrby(session_key(session_id), "total_segments", 1))

    async def increment_done_segments(self, session_id: str) -> int:
        return int(await self.r.hincrby(session_key(session_id), "done_segments", 1))

    async def acquire_completion_lock(self, session_id: str) -> bool:
        """Only one worker may run final stitching for a session."""
        ok = await self.r.set(completing_key(session_id), "1", nx=True, ex=120)
        return bool(ok)
