import os
import time
import asyncio
import subprocess
from typing import List

from app.jobs import JobStore
from app.events import EventBus
from app.audio import convert_to_wav_16k_mono, vad_segment_wav, match_audio_duration
from app.asr import transcribe
from app.aws_nlp import translate_text, tts_to_wav


def _concat_wavs_ffmpeg(segment_wavs: List[str], out_wav: str) -> None:
    """
    Concatenate wav segments using ffmpeg concat demuxer.
    """
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)
    list_file = out_wav + ".txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in segment_wavs:
            f.write(f"file '{p}'\n")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out_wav]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def process_job(store: JobStore, bus: EventBus, job_id: str, output_dir: str):
    store.update(job_id, status="running")
    await bus.publish(job_id, "status", {"status": "running"})

    try:
        job = store.get(job_id)

        job_out_dir = os.path.join(output_dir, job_id)
        os.makedirs(job_out_dir, exist_ok=True)

        # 1) Convert to canonical wav
        canon_wav = os.path.join(job_out_dir, "input_16k_mono.wav")
        await asyncio.to_thread(convert_to_wav_16k_mono, job.upload_path, canon_wav)

        # 2) VAD segmentation
        seg_dir = os.path.join(job_out_dir, "segments_in")
        segments = await asyncio.to_thread(vad_segment_wav, canon_wav, seg_dir)

        if not segments:
            raise RuntimeError("No speech detected")

        await bus.publish(job_id, "status", {"status": "segmented", "segments": len(segments)})

        # 3) Process each segment -> ASR -> translate -> TTS
        dubbed_segments: List[str] = []
        for seg in segments:
            t0 = time.time()

            # ASR
            asr_start = time.time()
            src_text = await asyncio.to_thread(transcribe, seg.path, job.src_lang, "small")
            asr_ms = int((time.time() - asr_start) * 1000)

            # MT
            mt_start = time.time()
            tgt_text = await asyncio.to_thread(translate_text, src_text, job.src_lang, job.tgt_lang)
            mt_ms = int((time.time() - mt_start) * 1000)

            # TTS
            tts_start = time.time()
            tts_path = os.path.join(job_out_dir, "segments_out", f"dub_{seg.index:04d}.wav")
            await asyncio.to_thread(tts_to_wav, tgt_text, job.voice, tts_path)
            tts_ms = int((time.time() - tts_start) * 1000)
            
            # Match TTS output duration to original segment duration
            original_duration_ms = seg.end_ms - seg.start_ms
            if original_duration_ms > 0:
                tts_matched_path = os.path.join(job_out_dir, "segments_out", f"dub_{seg.index:04d}_matched.wav")
                await asyncio.to_thread(match_audio_duration, tts_path, original_duration_ms, tts_matched_path)
                # Replace with matched version
                os.replace(tts_matched_path, tts_path)

            dubbed_segments.append(tts_path)
            job.segments.append(tts_path)
            store.update(job_id, segments=job.segments)

            await bus.publish(
                job_id,
                "segment",
                {
                    "segment_index": seg.index,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "src_text": src_text,
                    "tgt_text": tgt_text,
                    "audio_path": tts_path,
                    "asr_ms": asr_ms,
                    "mt_ms": mt_ms,
                    "tts_ms": tts_ms,
                    "total_ms": int((time.time() - t0) * 1000),
                },
            )

        # 4) Stitch final wav
        final_path = os.path.join(job_out_dir, "final.wav")
        await asyncio.to_thread(_concat_wavs_ffmpeg, dubbed_segments, final_path)

        store.update(job_id, status="done", output_path=final_path)
        await bus.publish(job_id, "done", {"output_path": final_path})
        await bus.publish(job_id, "status", {"status": "done"})

    except Exception as e:
        store.update(job_id, status="failed", error=str(e))
        await bus.publish(job_id, "status", {"status": "failed", "error": str(e)})

