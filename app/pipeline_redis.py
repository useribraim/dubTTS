import os
import time
import asyncio
import subprocess
import contextlib
from datetime import datetime
from typing import List

from app.redis_backend import RedisJobStore, RedisEventBus
from app.audio import convert_to_wav_16k_mono, vad_segment_wav_stream, match_audio_duration
from app.asr import transcribe
from app.aws_nlp import translate_text_async, tts_to_wav
from app.logging_config import get_logger, set_correlation_id

logger = get_logger(__name__)


async def _heartbeat_loop(store: RedisJobStore, job_id: str) -> None:
    interval = int(os.getenv("JOB_HEARTBEAT_INTERVAL", "5"))
    ttl_seconds = int(os.getenv("JOB_HEARTBEAT_TTL_SECONDS", "120"))
    while True:
        await store.update_heartbeat(job_id, datetime.utcnow().isoformat(), ttl_seconds)
        await asyncio.sleep(interval)


def _concat_wavs_ffmpeg(segment_wavs: List[str], out_wav: str) -> None:
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)
    list_file = out_wav + ".txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in segment_wavs:
            f.write(f"file '{p}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out_wav]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def process_job_redis(store: RedisJobStore, bus: RedisEventBus, job_id: str, output_root: str) -> bool:
    # Set correlation ID for logging
    set_correlation_id(job_id)
    
    job_start_time = time.time()
    first_segment_time = None
    heartbeat_task = asyncio.create_task(_heartbeat_loop(store, job_id))
    
    logger.info("Starting job processing", extra={"job_id": job_id, "operation": "process_job"})
    
    await store.update_job(job_id, status="running")
    await bus.publish(job_id, "status", {"status": "running"})

    try:
        job = await store.get_job(job_id)
        logger.info("Job loaded", extra={"job_id": job_id, "src_lang": job.get("src_lang"), "tgt_lang": job.get("tgt_lang")})

        job_out_dir = os.path.join(output_root, job_id)
        os.makedirs(job_out_dir, exist_ok=True)

        # Convert input to canonical wav
        convert_start = time.time()
        canon_wav = os.path.join(job_out_dir, "input_16k_mono.wav")
        await asyncio.to_thread(convert_to_wav_16k_mono, job["upload_path"], canon_wav)
        convert_ms = int((time.time() - convert_start) * 1000)
        logger.info("Audio converted", extra={"job_id": job_id, "duration_ms": convert_ms, "operation": "convert_audio"})

        seg_in_dir = os.path.join(job_out_dir, "segments_in")
        seg_out_dir = os.path.join(job_out_dir, "segments_out")
        os.makedirs(seg_out_dir, exist_ok=True)

        dubbed_segments: List[str] = []

        # Stream segments -> produce early SSE events
        idx = 0
        for seg in vad_segment_wav_stream(canon_wav, seg_in_dir):
            t0 = time.time()

            src_lang = job.get("src_lang", "en")
            tgt_lang = job.get("tgt_lang", "es")
            voice = job.get("voice", "Joanna")

            tts_path = os.path.join(seg_out_dir, f"dub_{idx:04d}.wav")
            existing_meta = await store.get_segment_meta(job_id, idx)
            if existing_meta and os.path.exists(tts_path):
                dubbed_segments.append(tts_path)
                await store.append_segment(job_id, tts_path, idx)
                existing_meta["audio_path"] = tts_path
                await bus.publish(job_id, "segment", existing_meta)
                logger.info(
                    "Segment reused from cache",
                    extra={"job_id": job_id, "segment_index": idx, "operation": "reuse_segment"},
                )
                if idx == 0 and first_segment_time is None:
                    first_segment_time = time.time()
                    time_to_first_segment_ms = int((first_segment_time - job_start_time) * 1000)
                    try:
                        from app.metrics import record_job_timing
                        await record_job_timing(job_id, "time_to_first_segment", time_to_first_segment_ms)
                    except Exception:
                        pass
                idx += 1
                continue

            asr_t0 = time.time()
            model_size = os.getenv("ASR_MODEL_SIZE", "small")
            src_text = await asyncio.to_thread(transcribe, seg.path, src_lang, model_size)
            asr_ms = int((time.time() - asr_t0) * 1000)

            # Record ASR latency metric
            try:
                from app.metrics import record_latency
                await record_latency("asr", asr_ms)
            except Exception:
                pass  # Don't break pipeline if metrics fail

            mt_t0 = time.time()
            # Use async translation with caching
            tgt_text = await translate_text_async(src_text, src_lang, tgt_lang)
            mt_ms = int((time.time() - mt_t0) * 1000)
            try:
                from app.metrics import record_latency
                await record_latency("translate", mt_ms)
            except Exception:
                pass
            
            # Log cache hit/miss (check if it was cached by comparing timing)
            cache_hit = mt_ms < 10  # Very fast = likely cache hit
            logger.debug(
                "Translation completed",
                extra={
                    "job_id": job_id,
                    "segment_index": idx,
                    "duration_ms": mt_ms,
                    "cache_hit": cache_hit,
                    "operation": "translate",
                }
            )
            try:
                from app.metrics import record_cache_hit
                await record_cache_hit("translate", cache_hit, mt_ms)
            except Exception:
                pass

            tts_t0 = time.time()
            
            # Use TTS provider abstraction (supports AWS, F5-TTS, or comparison)
            tts_provider = os.getenv("TTS_PROVIDER", "aws")
            tts_comparison_data = None
            
            try:
                from app.tts_providers import tts_with_provider
                output_path, tts_result = await asyncio.to_thread(
                    tts_with_provider,
                    tgt_text,
                    voice,
                    tts_path,
                    "16000",
                    tts_provider
                )
                
                # Handle comparison mode results
                if tts_provider == "comparison" and isinstance(tts_result, dict):
                    tts_comparison_data = tts_result
                    aws_metrics = tts_result.get("aws", {}).get("metrics", {})
                    f5_metrics = tts_result.get("f5_tts_russian", {}).get("metrics", {})
                    
                    logger.info(
                        "TTS comparison results",
                        extra={
                            "job_id": job_id,
                            "segment_index": idx,
                            "aws_duration_ms": aws_metrics.get("duration_ms"),
                            "aws_success": aws_metrics.get("success"),
                            "f5_duration_ms": f5_metrics.get("duration_ms"),
                            "f5_success": f5_metrics.get("success"),
                            "selected": tts_result.get("selected"),
                            "latency_diff_ms": tts_result.get("comparison", {}).get("latency_diff_ms"),
                            "operation": "tts_comparison",
                        }
                    )
                elif hasattr(tts_result, 'to_dict'):
                    # Single provider metrics
                    metrics_dict = tts_result.to_dict()
                    logger.debug(
                        f"TTS {metrics_dict['provider']} completed",
                        extra={
                            "job_id": job_id,
                            "segment_index": idx,
                            "duration_ms": metrics_dict.get("duration_ms"),
                            "operation": "tts",
                        }
                    )
            except ImportError:
                # Fallback if tts_providers module not available
                logger.debug("TTS providers module not available, using AWS directly")
                await asyncio.to_thread(tts_to_wav, tgt_text, voice, tts_path)
            except Exception as e:
                # Fallback to original AWS TTS on any error
                logger.warning(f"TTS provider failed, falling back to AWS: {e}", exc_info=True)
                await asyncio.to_thread(tts_to_wav, tgt_text, voice, tts_path)
            
            tts_ms = int((time.time() - tts_t0) * 1000)
            
            # Record TTS latency metric
            try:
                from app.metrics import record_latency
                await record_latency("tts", tts_ms)
            except Exception:
                pass  # Don't break pipeline if metrics fail
            
            # Optionally match TTS output duration to original segment duration
            # This can change tempo/speed, so disable for F5-TTS to preserve natural quality
            MATCH_DURATION = os.getenv("MATCH_TTS_DURATION", "false").lower() == "true"
            if MATCH_DURATION:
                original_duration_ms = seg.end_ms - seg.start_ms
                if original_duration_ms > 0:
                    tts_matched_path = os.path.join(seg_out_dir, f"dub_{idx:04d}_matched.wav")
                    await asyncio.to_thread(match_audio_duration, tts_path, original_duration_ms, tts_matched_path)
                    # Replace with matched version
                    os.replace(tts_matched_path, tts_path)
                    logger.debug(f"Matched TTS duration to original: {original_duration_ms}ms")
            else:
                logger.debug("Duration matching disabled - using natural TTS output")

            dubbed_segments.append(tts_path)
            await store.append_segment(job_id, tts_path, idx)

            total_ms = int((time.time() - t0) * 1000)
            
            # Record total segment processing latency
            try:
                from app.metrics import record_latency
                await record_latency("segment_total", total_ms)
            except Exception:
                pass  # Don't break pipeline if metrics fail
            
            logger.info(
                "Segment processed",
                extra={
                    "job_id": job_id,
                    "segment_index": idx,
                    "asr_ms": asr_ms,
                    "mt_ms": mt_ms,
                    "tts_ms": tts_ms,
                    "total_ms": total_ms,
                    "operation": "process_segment",
                }
            )
            
            # Prepare segment event data
            segment_event = {
                "segment_index": idx,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "src_text": src_text,
                "tgt_text": tgt_text,
                "audio_path": tts_path,
                "asr_ms": asr_ms,
                "mt_ms": mt_ms,
                "tts_ms": tts_ms,
                "total_ms": total_ms,
            }
            
            # Add TTS comparison data if available
            if tts_comparison_data:
                segment_event["tts_comparison"] = tts_comparison_data
            
            await store.set_segment_meta(job_id, idx, segment_event)
            await bus.publish(job_id, "segment", segment_event)
            
            # Track time-to-first-segment (incremental output benefit)
            if idx == 0 and first_segment_time is None:
                first_segment_time = time.time()
                time_to_first_segment_ms = int((first_segment_time - job_start_time) * 1000)
                try:
                    from app.metrics import record_job_timing
                    await record_job_timing(job_id, "time_to_first_segment", time_to_first_segment_ms)
                except Exception:
                    pass
            
            idx += 1

        if not dubbed_segments:
            raise RuntimeError("No speech detected")

        concat_start = time.time()
        final_path = os.path.join(job_out_dir, "final.wav")
        await asyncio.to_thread(_concat_wavs_ffmpeg, dubbed_segments, final_path)
        concat_ms = int((time.time() - concat_start) * 1000)
        
        # Track end-to-end latency
        end_to_end_ms = int((time.time() - job_start_time) * 1000)
        try:
            from app.metrics import record_job_timing
            await record_job_timing(job_id, "end_to_end", end_to_end_ms)
        except Exception:
            pass
        
        logger.info(
            "Job completed successfully",
            extra={
                "job_id": job_id,
                "segments_count": len(dubbed_segments),
                "concat_ms": concat_ms,
                "end_to_end_ms": end_to_end_ms,
                "time_to_first_segment_ms": int((first_segment_time - job_start_time) * 1000) if first_segment_time else None,
                "operation": "complete_job",
            }
        )

        await store.update_job(job_id, status="done", output_path=final_path)
        await bus.publish(job_id, "done", {"output_path": final_path})
        await bus.publish(job_id, "status", {"status": "done"})
        return True

    except Exception as e:
        logger.error(
            "Job failed",
            extra={
                "job_id": job_id,
                "error_type": type(e).__name__,
                "error": str(e),
                "operation": "process_job",
            },
            exc_info=True,
        )
        await store.update_job(job_id, status="retrying", error=str(e))
        await bus.publish(job_id, "status", {"status": "retrying", "error": str(e)})
        return False
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
