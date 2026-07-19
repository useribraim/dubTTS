"""
Segmenter process for the streaming pipeline.

Consumes session registrations from the sessions stream, then reads each
session's chunk stream while audio is still arriving, cuts ~SEGMENT_MS windows
of 16 kHz s16le mono PCM into segment WAV files, and enqueues traceable
segment tasks onto the ASR stage stream.

Run standalone:
    python -m app.segmenter
"""

import os
import asyncio
import socket
import uuid
import wave
from typing import Dict, Optional

import redis.asyncio as redis
from redis.exceptions import ResponseError

from app.logging_config import setup_logging, get_logger
from app.redis_backend import REDIS_URL, RedisEventBus
from app.streaming import (
    BYTES_PER_SECOND,
    CHUNK_STREAM_MAXLEN,
    SEGMENT_MS,
    SEG_STREAMS,
    SEG_STREAM_MAXLEN,
    SESSIONS_GROUP,
    SESSIONS_STREAM_KEY,
    StreamSessionStore,
    chunks_stream_key,
    make_envelope,
)

setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), json_output=os.getenv("LOG_JSON", "0") == "1")
logger = get_logger(__name__)

STREAMS_ROOT = os.getenv("STREAMS_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "streams"))
# Minimum tail worth emitting on finalize (avoid empty/tiny artifacts).
MIN_TAIL_MS = int(os.getenv("STREAM_MIN_TAIL_MS", "200"))


def _consumer_name() -> str:
    explicit = os.getenv("SEGMENTER_NAME")
    if explicit:
        return explicit
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def _write_pcm_wav(pcm: bytes, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm)


class Segmenter:
    def __init__(self, r: redis.Redis, consumer: str):
        self.r = r
        self.consumer = consumer
        self.store = StreamSessionStore(r)
        self.bus = RedisEventBus(r)
        self._tasks: Dict[str, asyncio.Task] = {}

    async def ensure_group(self) -> None:
        try:
            await self.r.xgroup_create(SESSIONS_STREAM_KEY, SESSIONS_GROUP, id="0-0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def run(self, stop_event: Optional[asyncio.Event] = None) -> None:
        await self.ensure_group()
        logger.info(
            "Segmenter started",
            extra={"consumer": self.consumer, "streams_root": STREAMS_ROOT, "operation": "segmenter_start"},
        )
        while stop_event is None or not stop_event.is_set():
            try:
                messages = await self.r.xreadgroup(
                    SESSIONS_GROUP,
                    self.consumer,
                    streams={SESSIONS_STREAM_KEY: ">"},
                    count=4,
                    block=2000,
                )
            except Exception as e:
                logger.error("Sessions stream read failed", extra={"error": str(e)}, exc_info=True)
                await asyncio.sleep(1)
                continue
            if not messages:
                continue
            for _stream, entries in messages:
                for entry_id, fields in entries:
                    session_id_raw = fields.get(b"session_id") or fields.get("session_id")
                    if not session_id_raw:
                        await self.r.xack(SESSIONS_STREAM_KEY, SESSIONS_GROUP, entry_id)
                        continue
                    session_id = session_id_raw.decode() if isinstance(session_id_raw, (bytes, bytearray)) else session_id_raw
                    if session_id not in self._tasks:
                        self._tasks[session_id] = asyncio.create_task(self._consume_session(session_id))
                    await self.r.xack(SESSIONS_STREAM_KEY, SESSIONS_GROUP, entry_id)

    async def _emit_segment(
        self,
        session: Dict[str, str],
        session_id: str,
        segment_index: int,
        pcm: bytes,
        onset_ts: float,
    ) -> None:
        seg_dir = os.path.join(STREAMS_ROOT, session_id, "segments_in")
        seg_path = os.path.join(seg_dir, f"seg_{segment_index:04d}.wav")
        await asyncio.to_thread(_write_pcm_wav, pcm, seg_path)

        envelope = make_envelope(
            session_id=session_id,
            segment_index=segment_index,
            audio_path=seg_path,
            src_lang=session.get("src_lang", "en"),
            tgt_lang=session.get("tgt_lang", "ru"),
            voice=session.get("voice", "Tatyana"),
            onset_ts=onset_ts,
        )
        await self.r.xadd(SEG_STREAMS["asr"], envelope, maxlen=SEG_STREAM_MAXLEN, approximate=True)
        total = await self.store.increment_total_segments(session_id)
        logger.info(
            "Segment enqueued to ASR",
            extra={
                "job_id": session_id,
                "segment_index": segment_index,
                "segment_id": envelope["segment_id"],
                "bytes": len(pcm),
                "total_segments": total,
                "operation": "emit_segment",
            },
        )

    async def _consume_session(self, session_id: str) -> None:
        """Read one session's chunk stream, cut segments, handle finalize."""
        import time

        stream_key = chunks_stream_key(session_id)
        last_id = "0-0"  # replay from start: chunks may predate segmenter pickup
        bytes_per_segment = int(BYTES_PER_SECOND * SEGMENT_MS / 1000)
        min_tail_bytes = int(BYTES_PER_SECOND * MIN_TAIL_MS / 1000)
        buffer = bytearray()
        segment_index = 0
        finalized = False

        try:
            session = await self.store.get_session(session_id)
        except KeyError:
            logger.error("Session vanished before segmentation", extra={"job_id": session_id})
            return

        logger.info("Segmenting session", extra={"job_id": session_id, "operation": "session_segment_start"})

        while not finalized:
            try:
                messages = await self.r.xread({stream_key: last_id}, count=64, block=2000)
            except Exception as e:
                logger.error(
                    "Chunk stream read failed",
                    extra={"job_id": session_id, "error": str(e)},
                    exc_info=True,
                )
                await asyncio.sleep(1)
                continue

            if not messages:
                # Idle: if the session was finalized elsewhere, flush and exit.
                try:
                    session = await self.store.get_session(session_id)
                    if session.get("finalized") == "1":
                        finalized = True
                except KeyError:
                    return
                continue

            for _stream, entries in messages:
                for entry_id, fields in entries:
                    last_id = entry_id.decode() if isinstance(entry_id, (bytes, bytearray)) else str(entry_id)
                    audio = fields.get(b"audio")
                    control = fields.get(b"control")

                    if audio:
                        buffer.extend(audio)
                        while len(buffer) >= bytes_per_segment:
                            chunk = bytes(buffer[:bytes_per_segment])
                            del buffer[:bytes_per_segment]
                            onset_ts = time.time()
                            await self._emit_segment(session, session_id, segment_index, chunk, onset_ts)
                            segment_index += 1

                    if control and control.decode() == "finalize":
                        finalized = True

            if finalized:
                tail = bytes(buffer)
                buffer.clear()
                if len(tail) >= min_tail_bytes:
                    onset_ts = time.time()
                    await self._emit_segment(session, session_id, segment_index, tail, onset_ts)
                    segment_index += 1

        await self.store.update_session(session_id, finalized="1")
        await self.bus.publish(session_id, "status", {"status": "finalized", "total_segments": segment_index})
        logger.info(
            "Session input finalized",
            extra={"job_id": session_id, "total_segments": segment_index, "operation": "session_finalized"},
        )

        # If everything already finished before finalize landed, trigger completion check.
        from app.stage_runner import maybe_complete_session

        await maybe_complete_session(self.r, session_id, self.bus)


async def run_segmenter(stop_event: Optional[asyncio.Event] = None) -> None:
    r = redis.from_url(REDIS_URL, decode_responses=False)
    segmenter = Segmenter(r, _consumer_name())
    await segmenter.run(stop_event=stop_event)


def main() -> None:
    asyncio.run(run_segmenter())


if __name__ == "__main__":
    main()
