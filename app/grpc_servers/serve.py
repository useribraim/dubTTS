"""
gRPC stage worker servers.

Each process serves exactly one stage (ASR / MT / TTS) so stages scale
independently. Workers are stateless compute services: audio/text in,
result out. All queueing, retries, ordering, and session bookkeeping live
in the Redis Streams dispatchers (app/dispatcher.py).

Run standalone:
    python -m app.grpc_servers.serve --stage asr --port 50051
    python -m app.grpc_servers.serve --stage mt  --port 50052
    python -m app.grpc_servers.serve --stage tts --port 50053
"""

import argparse
import asyncio
import os
import tempfile
import time
from concurrent import futures

import grpc

from app.logging_config import setup_logging, get_logger
from app.pb import dubbing_pb2, dubbing_pb2_grpc

setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), json_output=os.getenv("LOG_JSON", "0") == "1")
logger = get_logger(__name__)

GRPC_MAX_MSG = int(os.getenv("GRPC_MAX_MSG_BYTES", str(32 * 1024 * 1024)))

_SERVER_OPTIONS = [
    ("grpc.max_send_message_length", GRPC_MAX_MSG),
    ("grpc.max_receive_message_length", GRPC_MAX_MSG),
]


def _write_temp_wav(audio: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".wav")
    with os.fdopen(fd, "wb") as f:
        f.write(audio)
    return path


def _read_and_unlink(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    try:
        os.unlink(path)
    except OSError:
        pass
    return data


class TranscriptionServicer(dubbing_pb2_grpc.TranscriptionServiceServicer):
    async def Transcribe(self, request, context):
        from app.asr import transcribe

        t0 = time.time()
        wav_path = _write_temp_wav(request.audio)
        try:
            loop = asyncio.get_running_loop()
            model_size = os.getenv("ASR_MODEL_SIZE", "small")
            text = await loop.run_in_executor(
                None, transcribe, wav_path, request.lang or None, model_size
            )
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
        duration_ms = int((time.time() - t0) * 1000)
        logger.info(
            "gRPC Transcribe done",
            extra={"segment_id": request.segment_id, "duration_ms": duration_ms, "operation": "grpc_transcribe"},
        )
        return dubbing_pb2.TranscribeResponse(text=text, duration_ms=duration_ms)

    async def Health(self, request, context):
        model_size = os.getenv("ASR_MODEL_SIZE", "small")
        return dubbing_pb2.HealthResponse(ok=True, details=f"asr model={model_size}")


class TranslationServicer(dubbing_pb2_grpc.TranslationServiceServicer):
    async def Translate(self, request, context):
        from app.aws_nlp import translate_text_async
        from app.performance import get_cached_translation

        t0 = time.time()
        cache_hit = False
        if request.text:
            cached = await get_cached_translation(request.text, request.src_lang, request.tgt_lang)
            cache_hit = cached is not None
        # translate_text_async performs caching + latency/cache metrics itself.
        text = await translate_text_async(request.text, request.src_lang, request.tgt_lang)
        duration_ms = int((time.time() - t0) * 1000)
        logger.info(
            "gRPC Translate done",
            extra={
                "segment_id": request.segment_id,
                "duration_ms": duration_ms,
                "cache_hit": cache_hit,
                "operation": "grpc_translate",
            },
        )
        return dubbing_pb2.TranslateResponse(text=text, duration_ms=duration_ms, cache_hit=cache_hit)

    async def Health(self, request, context):
        return dubbing_pb2.HealthResponse(ok=True, details=f"mt use_aws={os.getenv('USE_AWS', '1')}")


class SynthesisServicer(dubbing_pb2_grpc.SynthesisServiceServicer):
    async def Synthesize(self, request, context):
        t0 = time.time()
        sample_rate = str(request.sample_rate or 16000)
        tts_provider = os.getenv("TTS_PROVIDER", "aws")
        fd, out_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            loop = asyncio.get_running_loop()

            def _synthesize() -> None:
                try:
                    from app.tts_providers import tts_with_provider

                    tts_with_provider(request.text, request.voice, out_path, sample_rate, tts_provider)
                except ImportError:
                    from app.aws_nlp import tts_to_wav

                    tts_to_wav(request.text, request.voice, out_path, sample_rate)

            await loop.run_in_executor(None, _synthesize)
            audio = _read_and_unlink(out_path)
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
        duration_ms = int((time.time() - t0) * 1000)
        logger.info(
            "gRPC Synthesize done",
            extra={"segment_id": request.segment_id, "duration_ms": duration_ms, "operation": "grpc_synthesize"},
        )
        return dubbing_pb2.SynthesizeResponse(audio=audio, duration_ms=duration_ms)

    async def Health(self, request, context):
        return dubbing_pb2.HealthResponse(ok=True, details=f"tts provider={os.getenv('TTS_PROVIDER', 'aws')}")


_SERVICERS = {
    "asr": (TranscriptionServicer, dubbing_pb2_grpc.add_TranscriptionServiceServicer_to_server),
    "mt": (TranslationServicer, dubbing_pb2_grpc.add_TranslationServiceServicer_to_server),
    "tts": (SynthesisServicer, dubbing_pb2_grpc.add_SynthesisServiceServicer_to_server),
}


async def serve(stage: str, port: int, stop_event: asyncio.Event | None = None) -> None:
    if stage not in _SERVICERS:
        raise ValueError(f"unknown stage: {stage}")
    servicer_cls, add_fn = _SERVICERS[stage]
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=int(os.getenv("GRPC_SERVER_THREADS", "4"))), options=_SERVER_OPTIONS)
    add_fn(servicer_cls(), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()

    metrics_port = int(os.getenv("METRICS_PORT", "0") or 0)
    if metrics_port:
        from prometheus_client import start_http_server

        start_http_server(metrics_port)

    logger.info(
        "gRPC worker started",
        extra={"stage": stage, "port": port, "metrics_port": metrics_port or None, "operation": "grpc_worker_start"},
    )
    if stop_event is None:
        await server.wait_for_termination()
    else:
        while not stop_event.is_set():
            await asyncio.sleep(0.2)
        await server.stop(grace=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a gRPC stage worker")
    parser.add_argument("--stage", required=True, choices=sorted(_SERVICERS.keys()))
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.stage, args.port))


if __name__ == "__main__":
    main()
