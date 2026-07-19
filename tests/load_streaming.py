"""
Phase 5 load harness: N concurrent WebSocket streaming sessions against a
running dubTTS stack (see docker-compose.full.yml), pacing PCM chunks to
real-time playback speed. Reports per-session completion, then reads the
aggregate onset->audio / TTFA / backlog / retry numbers off /v1/metrics.

Usage (from repo root):
    python -m tests.load_streaming --wav sample.wav --sessions 8 --api http://127.0.0.1:8000
"""

import argparse
import asyncio
import json
import sys
import time

import httpx

from tests.streaming_client import pcm_from_wav, stream_file


async def run_session(idx: int, api: str, wav_path: str, src_lang: str, tgt_lang: str, voice: str, chunk_ms: int) -> dict:
    started = time.time()
    try:
        stats = await stream_file(
            api_url=api,
            wav_path=wav_path,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            voice=voice,
            realtime=True,
            chunk_ms=chunk_ms,
        )
        stats["session_idx"] = idx
        stats["wall_s"] = round(time.time() - started, 3)
        stats["ok"] = True
    except Exception as exc:
        stats = {"session_idx": idx, "ok": False, "error": str(exc), "wall_s": round(time.time() - started, 3)}
    return stats


async def run_load(api: str, wav_path: str, sessions: int, src_lang: str, tgt_lang: str, voice: str, chunk_ms: int) -> dict:
    pcm_from_wav(wav_path)  # validate format up front, fail fast
    results = await asyncio.gather(
        *(run_session(i, api, wav_path, src_lang, tgt_lang, voice, chunk_ms) for i in range(sessions))
    )
    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{api}/v1/metrics")
        resp.raise_for_status()
        report = resp.json()

    return {
        "sessions_requested": sessions,
        "sessions_ok": len(ok),
        "sessions_failed": len(failed),
        "failures": failed,
        "per_session": results,
        "queue_delay": report.get("queue_delay"),
        "stage_backlog": report.get("stage_backlog"),
        "reliability": report.get("reliability"),
        "job_timing_metrics": report.get("job_timing_metrics"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Load-test the dubTTS streaming pipeline with concurrent WS sessions")
    parser.add_argument("--wav", required=True, help="16 kHz mono 16-bit WAV file to replay per session")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--sessions", type=int, default=8)
    parser.add_argument("--src-lang", default="en")
    parser.add_argument("--tgt-lang", default="ru")
    parser.add_argument("--voice", default="Tatyana")
    parser.add_argument("--chunk-ms", type=int, default=250)
    args = parser.parse_args()

    summary = asyncio.run(
        run_load(
            api=args.api,
            wav_path=args.wav,
            sessions=args.sessions,
            src_lang=args.src_lang,
            tgt_lang=args.tgt_lang,
            voice=args.voice,
            chunk_ms=args.chunk_ms,
        )
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["sessions_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
