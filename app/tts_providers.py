"""
TTS Provider Abstraction Layer

Supports multiple TTS providers:
- AWS Polly (cloud-based)
- F5-TTS_RUSSIAN (local HuggingFace model)
- Comparison mode (runs both and compares)
"""

import os
import time
import logging
import subprocess
import tempfile
from enum import Enum
from typing import Optional, Dict, Tuple
from pathlib import Path

from app.aws_nlp import tts_to_wav as aws_tts_to_wav, _write_silence_wav
from app.logging_config import get_logger

logger = get_logger(__name__)


class TTSProvider(Enum):
    """TTS provider types."""
    AWS = "aws"
    F5_TTS_RUSSIAN = "f5_tts_russian"
    COMPARISON = "comparison"  # Run both and compare


class TTSMetrics:
    """Metrics for TTS performance comparison."""
    
    def __init__(self, provider: str):
        self.provider = provider
        self.duration_ms: Optional[int] = None
        self.text_length: int = 0
        self.audio_size_bytes: int = 0
        self.sample_rate: int = 16000
        self.error: Optional[str] = None
        self.success: bool = False
    
    def to_dict(self) -> Dict:
        """Convert metrics to dictionary."""
        return {
            "provider": self.provider,
            "duration_ms": self.duration_ms,
            "text_length": self.text_length,
            "audio_size_bytes": self.audio_size_bytes,
            "sample_rate": self.sample_rate,
            "error": self.error,
            "success": self.success,
        }


# Configuration
F5_TTS_MODEL = os.getenv("F5_TTS_MODEL", "F5TTS_v1_Base")
F5_TTS_CKPT_DIR = os.getenv("F5_TTS_CKPT_DIR", "./models/f5-tts-russian")
F5_TTS_ENABLED = os.getenv("F5_TTS_ENABLED", "false").lower() == "true"
DEFAULT_TTS_PROVIDER = os.getenv("TTS_PROVIDER", "aws")  # "aws", "f5_tts_russian", "comparison"


def _check_f5_tts_available() -> bool:
    """Check if F5-TTS is available and configured."""
    if not F5_TTS_ENABLED:
        return False
    
    # Check if model directory exists
    ckpt_dir = Path(F5_TTS_CKPT_DIR)
    if not ckpt_dir.exists():
        logger.warning(f"F5-TTS checkpoint directory not found: {F5_TTS_CKPT_DIR}")
        return False
    
    # Check for required files (model files are in subdirectories)
    # Try to find model file in subdirectory based on F5_TTS_MODEL
    model_subdir = ckpt_dir / F5_TTS_MODEL
    
    # Vocab file can be in subdirectory or root (check subdirectory first)
    vocab_file = model_subdir / "vocab.txt"
    if not vocab_file.exists():
        vocab_file = ckpt_dir / "vocab.txt"  # Fallback to root
    
    # Look for inference model file (safetensors format, smaller)
    model_file = None
    possible_names = [
        "model_last_inference.safetensors",
        "model_240000_inference.safetensors",
        "model_last.safetensors",
        "model_last_inference.pt",
        "model_last.pt"
    ]
    
    if model_subdir.exists():
        for name in possible_names:
            candidate = model_subdir / name
            if candidate.exists():
                model_file = candidate
                break
    
    # Fallback: check root directory
    if model_file is None:
        for name in possible_names:
            candidate = ckpt_dir / name
            if candidate.exists():
                model_file = candidate
                break
    
    if model_file is None or not model_file.exists():
        logger.warning(f"F5-TTS model file not found. Checked {model_subdir} and {ckpt_dir}")
        logger.warning(f"Expected files: {possible_names}")
        return False
    
    if not vocab_file.exists():
        logger.warning(f"F5-TTS vocab file not found: {vocab_file}")
        return False
    
    # Check if f5-tts_infer-cli is available
    # Use shutil.which() for a quick check - don't actually run the command (it's too slow)
    import shutil
    if shutil.which("f5-tts_infer-cli") is None:
        logger.warning("f5-tts_infer-cli command not found in PATH. Install with: pip install git+https://github.com/SWivid/F5-TTS.git")
        return False
    
    return True


def tts_f5_russian(
    text: str,
    voice_id: str,
    out_wav_path: str,
    sample_rate: str = "16000"
) -> TTSMetrics:
    """
    Generate speech using F5-TTS_RUSSIAN model.
    
    Args:
        text: Russian text to synthesize
        voice_id: Voice identifier (not used for F5-TTS, kept for API compatibility)
        out_wav_path: Output WAV file path
        sample_rate: Target sample rate (F5-TTS typically uses 22050 or 24000)
    
    Returns:
        TTSMetrics object with performance data
    """
    metrics = TTSMetrics("f5_tts_russian")
    metrics.text_length = len(text)
    metrics.sample_rate = int(sample_rate)
    
    if not text:
        logger.warning(f"Empty text provided to F5-TTS, writing silence to {out_wav_path}")
        _write_silence_wav(out_wav_path, int(sample_rate), ms=200)
        metrics.success = True
        metrics.duration_ms = 0
        return metrics
    
    if not _check_f5_tts_available():
        error_msg = "F5-TTS not available or not configured"
        logger.error(error_msg)
        metrics.error = error_msg
        _write_silence_wav(out_wav_path, int(sample_rate), ms=600)
        return metrics
    
    start_time = time.time()
    
    try:
        os.makedirs(os.path.dirname(out_wav_path), exist_ok=True)
        
        # Prepare temporary output directory for F5-TTS
        output_dir = tempfile.mkdtemp(prefix="f5_tts_")
        output_file = Path(output_dir) / "output.wav"
        
        # Build F5-TTS command
        # Model files are in subdirectories: F5TTS_v1_Base/model_*.safetensors
        ckpt_dir = Path(F5_TTS_CKPT_DIR)
        model_subdir = ckpt_dir / F5_TTS_MODEL
        
        # Vocab file can be in subdirectory or root (check subdirectory first)
        vocab_file = model_subdir / "vocab.txt"
        if not vocab_file.exists():
            vocab_file = ckpt_dir / "vocab.txt"  # Fallback to root
        
        # Find model file (prefer inference safetensors, smaller and faster)
        model_file = None
        preferred_names = [
            "model_last_inference.safetensors",
            "model_240000_inference.safetensors",
            "model_last.safetensors",
            "model_last_inference.pt"
        ]
        
        if model_subdir.exists():
            for name in preferred_names:
                candidate = model_subdir / name
                if candidate.exists():
                    model_file = candidate
                    break
        
        # Fallback to root
        if model_file is None:
            for name in preferred_names:
                candidate = ckpt_dir / name
                if candidate.exists():
                    model_file = candidate
                    break
        
        if model_file is None or not model_file.exists():
            raise FileNotFoundError(
                f"F5-TTS model file not found. Checked {model_subdir} and {ckpt_dir}. "
                f"Expected one of: {preferred_names}"
            )
        
        # F5-TTS CLI command for checkpoint-based inference
        # Based on: https://github.com/SWivid/F5-TTS/tree/main/src/f5_tts/infer
        # For fine-tuned models like F5-TTS_RUSSIAN, we use checkpoint-based inference
        # with --ckpt_file and --vocab_file (not zero-shot with --ref_audio)
        # The CLI supports both -t (short) and --gen_text (long) for text input
        cmd = [
            "f5-tts_infer-cli",
            "--model", F5_TTS_MODEL,  # Model architecture variant: F5TTS_v1_Base, etc.
            "--ckpt_file", str(model_file),  # Checkpoint file path
            "--vocab_file", str(vocab_file),  # Vocabulary file path
            "-t", text,  # Text to synthesize (shorthand for --gen_text)
            "-o", output_dir,  # Output directory
            "-w", "output.wav",  # Output filename
            "--speed", "1.0",  # Speech speed multiplier (1.0 = normal speed)
            "--remove_silence"  # Remove leading/trailing silence
        ]
        
        # Note: F5-TTS typically outputs at 22050 or 24000 Hz
        # The output will be resampled to target sample_rate if needed during audio processing
        
        logger.debug(f"Running F5-TTS command: {' '.join(cmd)}")
        
        # Run F5-TTS inference
        # First inference can take 60-120+ seconds to load the model into memory
        # Subsequent inferences will be much faster (2-5 seconds)
        logger.info(f"Starting F5-TTS inference (this may take 60-120s on first run due to model loading)...")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180  # Increased to 180 seconds for first-time model loading
        )
        
        if result.returncode != 0:
            error_msg = f"F5-TTS inference failed (exit code {result.returncode})"
            logger.error(error_msg)
            logger.error(f"F5-TTS stderr: {result.stderr[:500]}")  # First 500 chars of error
            logger.error(f"F5-TTS stdout: {result.stdout[:500]}")  # First 500 chars of output
            metrics.error = error_msg
            _write_silence_wav(out_wav_path, int(sample_rate), ms=600)
            return metrics
        
        # Check if output file was created
        if not output_file.exists():
            error_msg = "F5-TTS did not generate output file"
            logger.error(error_msg)
            metrics.error = error_msg
            _write_silence_wav(out_wav_path, int(sample_rate), ms=600)
            return metrics
        
        # Copy output to target location
        import shutil
        shutil.copy2(output_file, out_wav_path)
        
        # Get file size
        metrics.audio_size_bytes = os.path.getsize(out_wav_path)
        metrics.duration_ms = int((time.time() - start_time) * 1000)
        metrics.success = True
        
        logger.info(
            f"F5-TTS generated audio: {metrics.audio_size_bytes} bytes in {metrics.duration_ms}ms"
        )
        
        # Cleanup temp directory
        try:
            shutil.rmtree(output_dir)
        except Exception as e:
            logger.warning(f"Failed to cleanup temp directory {output_dir}: {e}")
        
        return metrics
        
    except subprocess.TimeoutExpired as e:
        error_msg = "F5-TTS inference timed out after 180s. First inference can take longer due to model loading."
        logger.error(error_msg)
        logger.error(f"Command that timed out: {' '.join(cmd)}")
        logger.info("Tip: First F5-TTS inference can take 2-3 minutes. Subsequent inferences will be faster (2-5 seconds).")
        logger.info("Tip: Enable AWS (USE_AWS=1) for immediate audio output while F5-TTS loads.")
        metrics.error = error_msg
        _write_silence_wav(out_wav_path, int(sample_rate), ms=600)
        return metrics
    except Exception as e:
        error_msg = f"F5-TTS error: {type(e).__name__}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        metrics.error = error_msg
        _write_silence_wav(out_wav_path, int(sample_rate), ms=600)
        return metrics


def tts_aws_polly(
    text: str,
    voice_id: str,
    out_wav_path: str,
    sample_rate: str = "16000"
) -> TTSMetrics:
    """
    Generate speech using AWS Polly.
    
    Args:
        text: Text to synthesize
        voice_id: AWS Polly voice identifier
        out_wav_path: Output WAV file path
        sample_rate: Target sample rate
    
    Returns:
        TTSMetrics object with performance data
    """
    metrics = TTSMetrics("aws_polly")
    metrics.text_length = len(text)
    metrics.sample_rate = int(sample_rate)
    
    start_time = time.time()
    
    try:
        # Call existing AWS TTS function
        aws_tts_to_wav(text, voice_id, out_wav_path, sample_rate)
        
        # Check if file was created successfully
        if os.path.exists(out_wav_path):
            metrics.audio_size_bytes = os.path.getsize(out_wav_path)
            metrics.duration_ms = int((time.time() - start_time) * 1000)
            metrics.success = True
            
            logger.info(
                f"AWS Polly generated audio: {metrics.audio_size_bytes} bytes in {metrics.duration_ms}ms"
            )
        else:
            error_msg = "AWS Polly did not generate output file"
            metrics.error = error_msg
            metrics.success = False
            
    except Exception as e:
        error_msg = f"AWS Polly error: {type(e).__name__}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        metrics.error = error_msg
        metrics.success = False
    
    return metrics


def tts_with_provider(
    text: str,
    voice_id: str,
    out_wav_path: str,
    sample_rate: str = "16000",
    provider: Optional[str] = None
) -> Tuple[str, TTSMetrics]:
    """
    Generate speech using specified provider.
    
    Args:
        text: Text to synthesize
        voice_id: Voice identifier (provider-specific)
        out_wav_path: Output WAV file path
        sample_rate: Target sample rate
        provider: Provider name ("aws", "f5_tts_russian", "comparison")
                 If None, uses DEFAULT_TTS_PROVIDER env var
    
    Returns:
        Tuple of (output_path, metrics)
    """
    if provider is None:
        provider = DEFAULT_TTS_PROVIDER
    
    provider_enum = TTSProvider(provider)
    
    if provider_enum == TTSProvider.AWS:
        metrics = tts_aws_polly(text, voice_id, out_wav_path, sample_rate)
        return out_wav_path, metrics
    
    elif provider_enum == TTSProvider.F5_TTS_RUSSIAN:
        metrics = tts_f5_russian(text, voice_id, out_wav_path, sample_rate)
        return out_wav_path, metrics
    
    elif provider_enum == TTSProvider.COMPARISON:
        # Run both providers and compare
        return tts_comparison(text, voice_id, out_wav_path, sample_rate)
    
    else:
        raise ValueError(f"Unknown provider: {provider}")


def tts_comparison(
    text: str,
    voice_id: str,
    base_out_path: str,
    sample_rate: str = "16000"
) -> Tuple[str, Dict]:
    """
    Run TTS with both providers and compare performance.
    
    Args:
        text: Text to synthesize
        voice_id: Voice identifier
        base_out_path: Base output path (will create provider-specific files)
        sample_rate: Target sample rate
    
    Returns:
        Tuple of (selected_output_path, comparison_dict)
    """
    logger.info(f"Running TTS comparison for text: {text[:50]}...")
    
    # Check if AWS is enabled - if not, just use F5-TTS directly
    from app.aws_nlp import USE_AWS
    if not USE_AWS:
        logger.info("AWS is disabled (USE_AWS=0), using F5-TTS only for comparison")
        # Just run F5-TTS and return it
        f5_metrics = tts_f5_russian(text, voice_id, base_out_path, sample_rate)
        comparison = {
            "input_text": text,
            "text_length": len(text),
            "aws": {"available": False, "reason": "USE_AWS=0"},
            "f5_tts_russian": {
                "path": base_out_path,
                "metrics": f5_metrics.to_dict(),
                "available": f5_metrics.success
            },
            "selected": "f5_tts_russian" if f5_metrics.success else "none"
        }
        logger.info(f"F5-TTS only mode: {f5_metrics.duration_ms}ms, Success: {f5_metrics.success}")
        return base_out_path, comparison
    
    # Prepare output paths
    base_path = Path(base_out_path)
    aws_path = base_path.parent / f"{base_path.stem}_aws{base_path.suffix}"
    f5_path = base_path.parent / f"{base_path.stem}_f5{base_path.suffix}"
    
    # Run AWS Polly
    logger.debug("Running AWS Polly TTS...")
    aws_metrics = tts_aws_polly(text, voice_id, str(aws_path), sample_rate)
    
    # Run F5-TTS
    logger.debug("Running F5-TTS_RUSSIAN TTS...")
    f5_metrics = tts_f5_russian(text, voice_id, str(f5_path), sample_rate)
    
    # Compare results
    comparison = {
        "input_text": text,
        "text_length": len(text),
        "aws": {
            "path": str(aws_path),
            "metrics": aws_metrics.to_dict(),
            "available": aws_metrics.success
        },
        "f5_tts_russian": {
            "path": str(f5_path),
            "metrics": f5_metrics.to_dict(),
            "available": f5_metrics.success
        },
        "comparison": {
            "fastest": "aws" if (aws_metrics.duration_ms or float('inf')) < (f5_metrics.duration_ms or float('inf')) else "f5_tts_russian",
            "latency_diff_ms": (f5_metrics.duration_ms or 0) - (aws_metrics.duration_ms or 0),
            "size_diff_bytes": (f5_metrics.audio_size_bytes or 0) - (aws_metrics.audio_size_bytes or 0),
        }
    }
    
    # Select best result - prefer providers that actually generated audio (not silence)
    # Check if AWS actually generated audio (not just silence placeholder)
    aws_has_audio = aws_metrics.success and aws_metrics.audio_size_bytes and aws_metrics.audio_size_bytes > 20000  # Real audio is > 20KB
    f5_has_audio = f5_metrics.success and f5_metrics.audio_size_bytes and f5_metrics.audio_size_bytes > 20000
    
    if aws_has_audio and f5_has_audio:
        # Both have real audio - use faster one
        selected_path = aws_path if aws_metrics.duration_ms < f5_metrics.duration_ms else f5_path
        comparison["selected"] = "aws" if selected_path == aws_path else "f5_tts_russian"
        logger.info(f"Both providers generated audio. Selected faster: {comparison['selected']}")
    elif aws_has_audio:
        selected_path = aws_path
        comparison["selected"] = "aws"
        logger.info("Only AWS generated audio")
    elif f5_has_audio:
        selected_path = f5_path
        comparison["selected"] = "f5_tts_russian"
        logger.info("Only F5-TTS generated audio")
    else:
        # Both failed or only generated silence - use base path with silence
        selected_path = base_path
        _write_silence_wav(str(base_path), int(sample_rate), ms=600)
        comparison["selected"] = "none"
        logger.warning("Neither provider generated audio - using silence placeholder")
    
    # Copy selected to base path
    if selected_path.exists() and selected_path != base_path:
        import shutil
        shutil.copy2(selected_path, base_path)
    
    logger.info(
        f"TTS comparison complete. AWS: {aws_metrics.duration_ms}ms, "
        f"F5-TTS: {f5_metrics.duration_ms}ms, Selected: {comparison['selected']}"
    )
    
    return str(base_path), comparison
