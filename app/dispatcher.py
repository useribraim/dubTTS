"""
gRPC dispatchers for the streaming pipeline.

A dispatcher consumes segment tasks from its stage's Redis Stream with a
consumer group, delegates the compute to a pool of stateless gRPC worker
replicas (round-robin, per-call timeout, failover across replicas), writes
the result to the next stage stream, and acks. All queueing, bounded
retries, and session bookkeeping stay here; workers stay stateless.

Run standalone:
    ASR_WORKER_ADDRS=127.0.0.1:50051,127.0.0.1:50061 python -m app.dispatcher --stage asr
    MT_WORKER_ADDRS=127.0.0.1:50052  python -m app.dispatcher --stage mt
    TTS_WORKER_ADDRS=127.0.0.1:50053 python -m app.dispatcher --stage tts
"""

import argparse
import asyncio
import itertools
import os
import socket
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import grpc
import redis.asyncio as redis

from app.logging_config import setup_logging, get_logger
from app.metrics import record_latency
from app.pb import dubbing_pb2, dubbing_pb2_grpc
from app.redis_backend import REDIS_URL
from app.stage_runner import (
    StageContext,
    finalize_tts_segment,
    is_segment_done,
    run_stage_loop,
    tts_out_path,
)

setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), json_output=os.getenv("LOG_JSON", "0") == "1")
logger = get_logger(__name__)

GRPC_CALL_TIMEOUT_S = float(os.getenv("GRPC_CALL_TIMEOUT_S", "30"))
GRPC_MAX_MSG = int(os.getenv("GRPC_MAX_MSG_BYTES", str(32 * 1024 * 1024)))

_CHANNEL_OPTIONS = [
    ("grpc.max_send_message_length", GRPC_MAX_MSG),
    ("grpc.max_receive_message_length", GRPC_MAX_MSG),
]

_STAGE_ADDRS_ENV = {
    "asr": "ASR_WORKER_ADDRS",
    "mt": "MT_WORKER_ADDRS",
    "tts": "TTS_WORKER_ADDRS",
}


def _consumer_name(stage: str) -> str:
    explicit = os.getenv("DISPATCHER_NAME")
    if explicit:
        return explicit
    return f"{stage}-disp-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


class GrpcWorkerPool:
    """Round-robin pool of gRPC worker replicas for one stage."""

    def __init__(self, stage: str, addrs: List[str]):
        if not addrs:
            raise ValueError(f"no worker addresses configured for stage {stage}")
        self.stage = stage
        self.addrs = addrs
        self._channels: Dict[str, grpc.aio.Channel] = {}
        self._cycle = itertools.cycle(addrs)

    def _channel(self, addr: str) -> grpc.aio.Channel:
        channel = self._channels.get(addr)
        if channel is None:
            channel = grpc.aio.insecure_channel(addr, options=_CHANNEL_OPTIONS)
            self._channels[addr] = channel
        return channel

    def stub_for(self, addr: str):
        channel = self._channel(addr)
        if self.stage == "asr":
            return dubbing_pb2_grpc.TranscriptionServiceStub(channel)
        if self.stage == "mt":
            return dubbing_pb2_grpc.TranslationServiceStub(channel)
        return dubbing_pb2_grpc.SynthesisServiceStub(channel)

    def candidate_addrs(self) -> List[str]:
        """All replicas, rotated so each call starts at the next one."""
        n = len(self.addrs)
        start = next(self._cycle)
        idx = self.addrs.index(start)
        return [self.addrs[(idx + i) % n] for i in range(n)]

    async def call(self, method: str, request: Any) -> Any:
        """
        Invoke `method` on replicas in rotation until one answers.
        Raises the last error if every replica fails this attempt.
        """
        last_error: Optional[Exception] = None
        for addr in self.candidate_addrs():
            stub = self.stub_for(addr)
            try:
                return await asyncio.wait_for(
                    getattr(stub, method)(request), timeout=GRPC_CALL_TIMEOUT_S
                )
            except (grpc.RpcError, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning(
                    "gRPC replica call failed, failing over",
                    extra={
                        "stage": self.stage,
                        "addr": addr,
                        "error": str(e)[:200],
                        "operation": "grpc_failover",
                    },
                )
        raise last_error if last_error else RuntimeError(f"no replicas for {self.stage}")


def _pool_for(stage: str) -> GrpcWorkerPool:
    raw = os.getenv(_STAGE_ADDRS_ENV[stage], "")
    addrs = [a.strip() for a in raw.split(",") if a.strip()]
    return GrpcWorkerPool(stage, addrs)


def _handle_asr_grpc(pool: GrpcWorkerPool) -> Callable:
    async def handle(ctx: StageContext, env: Dict[str, str]) -> Dict[str, str]:
        loop = asyncio.get_running_loop()
        audio = await loop.run_in_executor(None, _read_file, env["audio_path"])
        request = dubbing_pb2.TranscribeRequest(
            audio=audio,
            lang=env.get("src_lang", ""),
            segment_id=env.get("segment_id", ""),
        )
        response = await pool.call("Transcribe", request)
        try:
            await record_latency("asr", response.duration_ms)
        except Exception:
            pass
        logger.info(
            "ASR stage done via gRPC",
            extra={
                "job_id": env["job_id"],
                "segment_id": env["segment_id"],
                "duration_ms": response.duration_ms,
                "operation": "stage_asr_grpc",
            },
        )
        return {"src_text": response.text, "asr_ms": str(response.duration_ms)}

    return handle


def _handle_mt_grpc(pool: GrpcWorkerPool) -> Callable:
    async def handle(ctx: StageContext, env: Dict[str, str]) -> Dict[str, str]:
        request = dubbing_pb2.TranslateRequest(
            text=env.get("src_text", ""),
            src_lang=env.get("src_lang", "en"),
            tgt_lang=env.get("tgt_lang", "ru"),
            segment_id=env.get("segment_id", ""),
        )
        response = await pool.call("Translate", request)
        try:
            await record_latency("translate", response.duration_ms)
        except Exception:
            pass
        logger.info(
            "MT stage done via gRPC",
            extra={
                "job_id": env["job_id"],
                "segment_id": env["segment_id"],
                "duration_ms": response.duration_ms,
                "cache_hit": response.cache_hit,
                "operation": "stage_mt_grpc",
            },
        )
        return {"tgt_text": response.text, "mt_ms": str(response.duration_ms)}

    return handle


def _handle_tts_grpc(pool: GrpcWorkerPool) -> Callable:
    async def handle(ctx: StageContext, env: Dict[str, str]) -> Dict[str, str]:
        job_id = env["job_id"]
        idx = int(env["segment_index"])
        tts_path = tts_out_path(job_id, idx)

        if await is_segment_done(ctx, job_id, idx, tts_path):
            existing_meta = await ctx.store.get_segment_meta(job_id, idx)
            await ctx.bus.publish(job_id, "segment", existing_meta)
            logger.info(
                "Segment already synthesized, replayed from state",
                extra={"job_id": job_id, "segment_id": env["segment_id"], "operation": "stage_tts_replay"},
            )
            return {}

        request = dubbing_pb2.SynthesizeRequest(
            text=env.get("tgt_text", ""),
            voice=env.get("voice", "Tatyana"),
            sample_rate=16000,
            segment_id=env.get("segment_id", ""),
        )
        response = await pool.call("Synthesize", request)
        os.makedirs(os.path.dirname(tts_path), exist_ok=True)
        with open(tts_path, "wb") as f:
            f.write(response.audio)
        try:
            await record_latency("tts", response.duration_ms)
        except Exception:
            pass

        await finalize_tts_segment(ctx, env, tts_path, response.duration_ms)
        return {}

    return handle


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


_GRPC_HANDLERS = {
    "asr": _handle_asr_grpc,
    "mt": _handle_mt_grpc,
    "tts": _handle_tts_grpc,
}


async def run_dispatcher(stage: str, stop_event: Optional[asyncio.Event] = None) -> None:
    if stage not in _GRPC_HANDLERS:
        raise ValueError(f"unknown stage: {stage}")
    pool = _pool_for(stage)
    r = redis.from_url(REDIS_URL, decode_responses=False)
    ctx = StageContext(r, stage)
    logger.info(
        "Dispatcher starting",
        extra={"stage": stage, "workers": pool.addrs, "operation": "dispatcher_start"},
    )
    await run_stage_loop(
        stage,
        _GRPC_HANDLERS[stage](pool),
        stop_event=stop_event,
        ctx=ctx,
        consumer=_consumer_name(stage),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a gRPC stage dispatcher")
    parser.add_argument("--stage", required=True, choices=sorted(_GRPC_HANDLERS.keys()))
    args = parser.parse_args()
    asyncio.run(run_dispatcher(args.stage))


if __name__ == "__main__":
    main()
