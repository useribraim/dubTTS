import os
from functools import lru_cache
from typing import Optional, Any

from faster_whisper import WhisperModel


def _optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _optional_bool(value: Optional[str]) -> Optional[bool]:
    if value is None or value == "":
        return None
    return value.strip().lower() in {"1", "true", "yes", "y"}


@lru_cache(maxsize=1)
def get_whisper_model(model_size: Optional[str] = None) -> WhisperModel:
    """
    Lazily load and cache a single Whisper model instance.

    Use "base"/"small" for MVP speed; scale up to "medium"/"large-v3" later.
    """
    model_size = model_size or os.getenv("ASR_MODEL_SIZE", "small")
    device = os.getenv("ASR_DEVICE", "cpu")
    compute_type = os.getenv("ASR_COMPUTE_TYPE", "int8")
    cpu_threads = _optional_int(os.getenv("ASR_CPU_THREADS"))
    num_workers = _optional_int(os.getenv("ASR_NUM_WORKERS"))
    kwargs: dict[str, Any] = {
        "device": device,
        "compute_type": compute_type,
    }
    if cpu_threads is not None:
        kwargs["cpu_threads"] = cpu_threads
    if num_workers is not None:
        kwargs["num_workers"] = num_workers
    return WhisperModel(model_size, **kwargs)


def transcribe(
    wav_path: str,
    lang: Optional[str] = None,
    model_size: Optional[str] = None,
) -> str:
    """
    Run transcription on a single WAV file and return the concatenated text.
    """
    model = get_whisper_model(model_size)
    beam_size = _optional_int(os.getenv("ASR_BEAM_SIZE"))
    best_of = _optional_int(os.getenv("ASR_BEST_OF"))
    temperature = _optional_float(os.getenv("ASR_TEMPERATURE"))
    condition_on_previous_text = _optional_bool(os.getenv("ASR_CONDITION_ON_PREV_TEXT"))
    vad_filter = _optional_bool(os.getenv("ASR_VAD_FILTER"))
    chunk_length = _optional_int(os.getenv("ASR_CHUNK_LENGTH"))
    transcribe_kwargs: dict[str, Any] = {
        "wav_path": wav_path,
        "language": lang,
        "vad_filter": False if vad_filter is None else vad_filter,
    }
    if beam_size is not None:
        transcribe_kwargs["beam_size"] = beam_size
    if best_of is not None:
        transcribe_kwargs["best_of"] = best_of
    if temperature is not None:
        transcribe_kwargs["temperature"] = temperature
    if condition_on_previous_text is not None:
        transcribe_kwargs["condition_on_previous_text"] = condition_on_previous_text
    if chunk_length is not None:
        transcribe_kwargs["chunk_length"] = chunk_length

    segments, _info = model.transcribe(
        wav_path,
        **{k: v for k, v in transcribe_kwargs.items() if k != "wav_path"},
    )
    text = "".join([s.text for s in segments]).strip()
    return text

