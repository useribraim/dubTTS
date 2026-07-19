"""
WebSocket streaming client for the dubTTS streaming pipeline.

Usable as a library (tests, load harness) or standalone:

    python tests/streaming_client.py --wav input.wav --api http://127.0.0.1:8000 \
        --src-lang en --tgt-lang ru --voice Tatyana [--realtime]

The WAV must be 16 kHz mono 16-bit PCM (use ffmpeg to convert first).
"""

import argparse
import asyncio
import json
import sys
import time
import wave
from typing import Optional

import httpx
import websockets


def pcm_from_wav(wav_path: str) -> bytes:
    """Read raw PCM frames from a 16 kHz mono 16-bit WAV file."""
    with wave.open(wav_path, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
            raise ValueError(
                f"{wav_path} must be 16 kHz mono 16-bit PCM WAV "
                f"(got channels={wf.getnchannels()}, width={wf.getsampwidth()}, rate={wf.getframerate()})"
            )
        return wf.readframes(wf.getnframes())


def create_session(api_url: str, src_lang: str, tgt_lang: str, voice: str) -> str:
    resp = httpx.post(
        f"{api_url}/v1/streams",
        data={"src_lang": src_lang, "tgt_lang": tgt_lang, "voice": voice},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["session_id"]


async def stream_pcm(
    ws_url: str,
    pcm: bytes,
    realtime: bool = False,
    chunk_ms: int = 250,
    finalize: bool = True,
) -> dict:
    """
    Send PCM audio to the streaming endpoint.

    realtime=True paces chunks to wall-clock playback speed (load harness);
    realtime=False sends as fast as possible (tests).
    Returns timing stats.
    """
    bytes_per_ms = 32  # 16 kHz * 2 bytes
    chunk_bytes = max(2, bytes_per_ms * chunk_ms)
    started = time.time()
    sent_bytes = 0

    async with websockets.connect(ws_url, max_size=None) as ws:
        for offset in range(0, len(pcm), chunk_bytes):
            chunk = pcm[offset : offset + chunk_bytes]
            await ws.send(chunk)
            sent_bytes += len(chunk)
            if realtime:
                expected = started + sent_bytes / (bytes_per_ms * 1000.0)
                delay = expected - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
        if finalize:
            await ws.send(json.dumps({"type": "finalize"}))
            try:
                await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    return {
        "sent_bytes": sent_bytes,
        "duration_s": round(time.time() - started, 3),
        "audio_seconds": round(len(pcm) / (bytes_per_ms * 1000.0), 3),
    }


async def stream_file(
    api_url: str,
    wav_path: str,
    src_lang: str = "en",
    tgt_lang: str = "ru",
    voice: str = "Tatyana",
    realtime: bool = False,
    chunk_ms: int = 250,
) -> dict:
    """Create a session and stream a WAV file into it. Returns session info."""
    pcm = pcm_from_wav(wav_path)
    session_id = create_session(api_url, src_lang, tgt_lang, voice)
    ws_url = api_url.replace("http://", "ws://").replace("https://", "wss://")
    stats = await stream_pcm(f"{ws_url}/v1/streams/{session_id}/audio", pcm, realtime=realtime, chunk_ms=chunk_ms)
    stats["session_id"] = session_id
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream a WAV file into the dubTTS streaming pipeline")
    parser.add_argument("--wav", required=True, help="16 kHz mono 16-bit WAV file")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--src-lang", default="en")
    parser.add_argument("--tgt-lang", default="ru")
    parser.add_argument("--voice", default="Tatyana")
    parser.add_argument("--realtime", action="store_true", help="Pace chunks to playback speed")
    parser.add_argument("--chunk-ms", type=int, default=250)
    args = parser.parse_args()

    stats = asyncio.run(
        stream_file(
            api_url=args.api,
            wav_path=args.wav,
            src_lang=args.src_lang,
            tgt_lang=args.tgt_lang,
            voice=args.voice,
            realtime=args.realtime,
            chunk_ms=args.chunk_ms,
        )
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
