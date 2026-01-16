import os
import wave
import logging

from botocore.exceptions import BotoCoreError, ClientError

from app.performance import get_cached_translation, set_cached_translation
from app.aws_credentials import get_aws_client, CredentialsError, AWS_REGION, USE_AWS

logger = logging.getLogger(__name__)

logger.info(f"AWS NLP module loaded: USE_AWS={USE_AWS}, AWS_REGION={AWS_REGION}")

# Initialize AWS clients lazily (on first use) to handle credential errors gracefully
_translate = None
_polly = None


def _get_translate_client():
    """Get or create AWS Translate client with proper credentials."""
    global _translate
    if _translate is None and USE_AWS:
        try:
            _translate = get_aws_client("translate", region_name=AWS_REGION)
            logger.info("AWS Translate client initialized")
        except CredentialsError as e:
            logger.error(f"Failed to initialize Translate client: {e}")
            _translate = None
    return _translate


def _get_polly_client():
    """Get or create AWS Polly client with proper credentials."""
    global _polly
    if _polly is None and USE_AWS:
        try:
            _polly = get_aws_client("polly", region_name=AWS_REGION)
            logger.info("AWS Polly client initialized")
        except CredentialsError as e:
            logger.error(f"Failed to initialize Polly client: {e}")
            _polly = None
    return _polly


def translate_text(text: str, src_lang: str, tgt_lang: str) -> str:
    if not text:
        logger.debug("Empty text provided to translate, returning empty string")
        return ""
    
    if not USE_AWS:
        logger.warning(f"USE_AWS=False, skipping translation, returning original text: {text[:50]}...")
        return text

    translate_client = _get_translate_client()
    if translate_client is None:
        logger.error("Translate client is None! Returning original text")
        return text

    try:
        logger.info(f"Calling AWS Translate: {src_lang} -> {tgt_lang}, text: '{text[:50]}...'")
        resp = translate_client.translate_text(
            Text=text,
            SourceLanguageCode=src_lang,
            TargetLanguageCode=tgt_lang,
        )
        translated = resp["TranslatedText"]
        logger.info(f"Translation successful: '{translated[:50]}...'")
        return translated
    except (BotoCoreError, ClientError) as e:
        logger.error(f"AWS Translate error: {type(e).__name__}: {str(e)}", exc_info=True)
        logger.warning(f"Returning original text due to translation error: {text[:50]}...")
        return text


def tts_to_wav(text: str, voice_id: str, out_wav_path: str, sample_rate: str = "16000") -> None:
    """
    Polly PCM -> WAV container. If USE_AWS=0, writes a short silence wav as placeholder.
    """
    
    os.makedirs(os.path.dirname(out_wav_path), exist_ok=True)

    if not text:
        logger.warning(f"Empty text provided to TTS, writing 200ms silence to {out_wav_path}")
        _write_silence_wav(out_wav_path, int(sample_rate), ms=200)
        return

    if not USE_AWS:
        logger.warning(f"USE_AWS=0, writing 600ms silence placeholder to {out_wav_path}")
        _write_silence_wav(out_wav_path, int(sample_rate), ms=600)
        return

    polly_client = _get_polly_client()
    if polly_client is None:
        logger.error("Polly client is None but USE_AWS=1. Check AWS credentials.")
        _write_silence_wav(out_wav_path, int(sample_rate), ms=600)
        return

    try:
        logger.debug(f"Calling Polly TTS: voice={voice_id}, text_length={len(text)}, text_preview={text[:50]}...")
        resp = polly_client.synthesize_speech(
            Text=text,
            OutputFormat="pcm",
            VoiceId=voice_id,
            SampleRate=sample_rate,
        )
        pcm_stream = resp["AudioStream"].read()
        
        if not pcm_stream or len(pcm_stream) == 0:
            logger.error(f"Polly returned empty audio stream for text: {text[:50]}...")
            _write_silence_wav(out_wav_path, int(sample_rate), ms=600)
            return

        logger.debug(f"Polly generated {len(pcm_stream)} bytes of audio")
        with wave.open(out_wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(int(sample_rate))
            wf.writeframes(pcm_stream)
        
        logger.debug(f"Successfully wrote TTS audio to {out_wav_path}")

    except (BotoCoreError, ClientError) as e:
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "Unknown") if hasattr(e, "response") else "Unknown"
        logger.error(
            f"AWS Polly error ({error_code}): {str(e)}. Writing 600ms silence to {out_wav_path}",
            exc_info=True
        )
        _write_silence_wav(out_wav_path, int(sample_rate), ms=600)
    except Exception as e:
        logger.error(
            f"Unexpected error in TTS: {type(e).__name__}: {str(e)}. Writing 600ms silence to {out_wav_path}",
            exc_info=True
        )
        _write_silence_wav(out_wav_path, int(sample_rate), ms=600)


async def translate_text_async(text: str, src_lang: str, tgt_lang: str) -> str:
    """
    Async version of translate_text with Redis caching.
    Checks cache first, then calls AWS Translate if needed.
    """
    if not text:
        return ""
    
    import time
    from app.metrics import record_latency, record_cache_hit
    
    start_time = time.time()
    
    # Check cache first
    cached = await get_cached_translation(text, src_lang, tgt_lang)
    if cached:
        latency_ms = (time.time() - start_time) * 1000
        await record_latency("translate", latency_ms)
        await record_cache_hit("translate", True, latency_ms)  # Record actual latency
        return cached
    
    # Cache miss - call AWS Translate
    result = translate_text(text, src_lang, tgt_lang)
    latency_ms = (time.time() - start_time) * 1000
    
    # Record metrics
    await record_latency("translate", latency_ms)
    await record_cache_hit("translate", False, latency_ms)  # Record actual latency
    
    # Cache the result (async, non-blocking)
    await set_cached_translation(text, src_lang, tgt_lang, result)
    
    return result


def _write_silence_wav(out_path: str, sr: int, ms: int = 500) -> None:
    n_samples = int(sr * (ms / 1000.0))
    silence = b"\x00\x00" * n_samples
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(silence)

