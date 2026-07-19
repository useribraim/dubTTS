"""
Tests for the gRPC stage workers and the Redis Streams dispatchers.

Covers:
- servicer round-trips over real gRPC channels
- dispatcher failover across replicas (dead replica skipped)
- full dispatcher-driven streaming session (WS -> segmenter -> gRPC stages)
"""

import asyncio
import json
import math
import os
import struct
import threading
import time
from concurrent import futures

import grpc
import pytest
import redis as sync_redis
from fastapi.testclient import TestClient

from app.grpc_servers.serve import _SERVICERS
from app.pb import dubbing_pb2, dubbing_pb2_grpc

os.environ.setdefault("ASR_MODEL_SIZE", "tiny")
os.environ.setdefault("STREAM_SEGMENT_MS", "5000")


def _make_pcm(seconds: float, freq: float = 440.0, rate: int = 16000) -> bytes:
    frames = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(frames)
    )


def _make_wav_bytes(seconds: float = 2.0) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(_make_pcm(seconds))
    return buf.getvalue()


@pytest.fixture(scope="module")
def grpc_cluster():
    """
    Start one gRPC server per stage plus the segmenter and three dispatchers
    in a background thread loop. Yields {stage: addr} for direct stub calls.
    """
    from app.dispatcher import run_dispatcher
    from app.segmenter import run_segmenter

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

        async def setup():
            servers = []
            addrs = {}
            for stage, (servicer_cls, add_fn) in _SERVICERS.items():
                server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=4))
                add_fn(servicer_cls(), server)
                port = server.add_insecure_port("127.0.0.1:0")
                await server.start()
                servers.append(server)
                addrs[stage] = f"127.0.0.1:{port}"
            os.environ["ASR_WORKER_ADDRS"] = addrs["asr"]
            os.environ["MT_WORKER_ADDRS"] = addrs["mt"]
            os.environ["TTS_WORKER_ADDRS"] = addrs["tts"]
            holder["servers"] = servers
            holder["addrs"] = addrs
            tasks = [loop.create_task(run_segmenter())]
            tasks += [loop.create_task(run_dispatcher(stage)) for stage in ("asr", "mt", "tts")]
            return tasks

        holder["tasks"] = loop.run_until_complete(setup())
        started.set()
        try:
            loop.run_forever()
        finally:
            for task in holder["tasks"]:
                task.cancel()
            for server in holder["servers"]:
                server.stop(None)
            loop.run_until_complete(asyncio.gather(*holder["tasks"], return_exceptions=True))
            loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(timeout=10)
    time.sleep(1.0)  # consumer groups + dispatcher startup
    yield holder
    holder["loop"].call_soon_threadsafe(holder["loop"].stop)
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_translation_servicer_roundtrip(grpc_cluster):
    async def _call():
        addr = grpc_cluster["addrs"]["mt"]
        async with grpc.aio.insecure_channel(addr) as channel:
            stub = dubbing_pb2_grpc.TranslationServiceStub(channel)
            health = await stub.Health(dubbing_pb2.HealthRequest())
            assert health.ok
            resp = await stub.Translate(
                dubbing_pb2.TranslateRequest(text="hello world", src_lang="en", tgt_lang="ru", segment_id="t:0")
            )
            assert resp.text  # USE_AWS=0 passthrough returns source text
            assert resp.duration_ms >= 0

    asyncio.run(_call())


def test_synthesis_servicer_roundtrip(grpc_cluster):
    async def _call():
        addr = grpc_cluster["addrs"]["tts"]
        async with grpc.aio.insecure_channel(addr) as channel:
            stub = dubbing_pb2_grpc.SynthesisServiceStub(channel)
            health = await stub.Health(dubbing_pb2.HealthRequest())
            assert health.ok
            resp = await stub.Synthesize(
                dubbing_pb2.SynthesizeRequest(text="hello", voice="Tatyana", sample_rate=16000, segment_id="t:1")
            )
            assert resp.audio[:4] == b"RIFF"  # WAV container

    asyncio.run(_call())


def test_transcription_servicer_roundtrip(grpc_cluster):
    async def _call():
        addr = grpc_cluster["addrs"]["asr"]
        async with grpc.aio.insecure_channel(addr) as channel:
            stub = dubbing_pb2_grpc.TranscriptionServiceStub(channel)
            health = await stub.Health(dubbing_pb2.HealthRequest())
            assert health.ok
            resp = await stub.Transcribe(
                dubbing_pb2.TranscribeRequest(audio=_make_wav_bytes(2.0), lang="en", segment_id="t:2")
            )
            assert isinstance(resp.text, str)  # sine wave -> likely empty, must not error

    asyncio.run(_call())


def test_dispatcher_failover_skips_dead_replica(grpc_cluster):
    from app.dispatcher import GrpcWorkerPool

    live = grpc_cluster["addrs"]["mt"]
    pool = GrpcWorkerPool("mt", ["127.0.0.1:9", live])  # port 9 is dead

    async def _call():
        return await pool.call(
            "Translate",
            dubbing_pb2.TranslateRequest(text="failover", src_lang="en", tgt_lang="ru", segment_id="t:3"),
        )

    resp = asyncio.run(_call())
    assert resp.text == "failover"


def test_dispatcher_end_to_end_session(client: TestClient, grpc_cluster):
    resp = client.post("/v1/streams", data={"src_lang": "en", "tgt_lang": "ru", "voice": "Tatyana"})
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    pcm = _make_pcm(12.0)
    with client.websocket_connect(f"/v1/streams/{session_id}/audio") as ws:
        chunk = 8000
        for offset in range(0, len(pcm), chunk):
            ws.send_bytes(pcm[offset : offset + chunk])
        ws.send_json({"type": "finalize"})
        assert ws.receive_json() == {"type": "finalized"}

    deadline = time.time() + 120
    status = None
    while time.time() < deadline:
        status = client.get(f"/v1/streams/{session_id}").json()
        if status["status"] in ("done", "failed"):
            break
        time.sleep(1.0)

    assert status["status"] == "done", f"session failed: {status}"
    assert status["total_segments"] == 3
    assert status["done_segments"] == 3

    rc = sync_redis.Redis.from_url(os.environ["REDIS_URL"])
    events = rc.xrange(f"dub:job:{session_id}:events_stream")
    seg_events = [e for e in events if (e[1].get(b"type") or b"").decode() == "segment"]
    assert len(seg_events) == 3
    first = json.loads(seg_events[0][1][b"data"].decode())
    assert first["segment_id"] == f"{session_id}:0"
