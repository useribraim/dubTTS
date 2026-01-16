# F5-TTS_RUSSIAN Setup and Performance Comparison Guide

This guide explains how to set up and use the F5-TTS_RUSSIAN model alongside AWS Polly for performance comparison.

## Overview

The application now supports multiple TTS providers:
- **AWS Polly** (cloud-based, production-ready)
- **F5-TTS_RUSSIAN** (local HuggingFace model, optimized for Russian)
- **Comparison Mode** (runs both and compares performance)

## F5-TTS_RUSSIAN Model

[F5-TTS_RUSSIAN](https://huggingface.co/Misha24-10/F5-TTS_RUSSIAN) is a fine-tuned version of F5-TTS specifically optimized for Russian text-to-speech synthesis.

**Key Features:**
- Trained on 5,000+ hours of Russian and English speech
- Supports accent control (use `+` before stressed vowel: `молок+о`)
- Multiple model variants available
- High-quality Russian voice synthesis

## Installation

### Step 1: Install F5-TTS

```bash
pip install git+https://github.com/SWivid/F5-TTS.git
```

### Step 2: Download Model Weights

```bash
# Install huggingface-cli if not already installed
pip install huggingface-hub

# Download the model
huggingface-cli download Misha24-10/F5-TTS_RUSSIAN \
  --local-dir ./models/f5-tts-russian \
  --local-dir-use-symlinks False
```

This will download:
- `model_last.safetensors` - Model weights
- `vocab.txt` - Vocabulary file
- Other model files

### Step 3: Verify Installation

```bash
# Test F5-TTS CLI
f5-tts_infer-cli --help

# Test with a simple Russian text
f5-tts_infer-cli \
  --model F5TTS_v1_Base \
  --ckpt_file "./models/f5-tts-russian/model_last.safetensors" \
  --vocab_file "./models/f5-tts-russian/vocab.txt" \
  -t "Привет, это тест." \
  -o "./test_output" \
  -w "test.wav" \
  --speed 1.0 \
  --remove_silence
```

## Configuration

### Environment Variables

```bash
# Enable F5-TTS
export F5_TTS_ENABLED="true"

# Set model directory (default: ./models/f5-tts-russian)
export F5_TTS_CKPT_DIR="./models/f5-tts-russian"

# Set model variant (default: F5TTS_v1_Base)
# Options: F5TTS_v1_Base, F5TTS_v1_Base_accent_tune, F5TTS_v1_Base_v2
export F5_TTS_MODEL="F5TTS_v1_Base"

# Select TTS provider
# Options: "aws", "f5_tts_russian", "comparison"
export TTS_PROVIDER="comparison"  # Run both and compare
```

### Model Variants

1. **F5TTS_v1_Base** - Original version, good general quality
2. **F5TTS_v1_Base_accent_tune** - Full accent markup support (recommended for best quality)
3. **F5TTS_v1_Base_v2** - Improved version with data filtering (+16 epochs)

## Usage

### Option 1: Use F5-TTS Only

```bash
export TTS_PROVIDER="f5_tts_russian"
export F5_TTS_ENABLED="true"
python -m app.worker
```

### Option 2: Use AWS Polly Only (Default)

```bash
export TTS_PROVIDER="aws"
export USE_AWS="1"
python -m app.worker
```

### Option 3: Comparison Mode (Recommended)

```bash
export TTS_PROVIDER="comparison"
export USE_AWS="1"
export F5_TTS_ENABLED="true"
python -m app.worker
```

In comparison mode, the system will:
1. Generate audio with both providers
2. Compare performance metrics (latency, file size)
3. Log comparison results
4. Use the faster provider's output (or AWS by default if both succeed)

## Performance Comparison

### Metrics Tracked

When using comparison mode, the following metrics are logged:

- **Latency**: Time to generate audio (milliseconds)
- **File Size**: Output audio file size (bytes)
- **Success Rate**: Whether generation succeeded
- **Quality**: (Manual assessment - can add automated metrics)

### Viewing Comparison Results

Comparison results are logged in the worker output:

```
[INFO] TTS comparison results job_id=abc123 segment_index=0 
  aws_duration_ms=850 aws_success=True 
  f5_duration_ms=1200 f5_success=True 
  selected=aws latency_diff_ms=350
```

### SSE Event Data

When using comparison mode, segment events include comparison data:

```json
{
  "segment_index": 0,
  "tts_comparison": {
    "aws": {
      "path": "/path/to/dub_0000_aws.wav",
      "metrics": {
        "provider": "aws_polly",
        "duration_ms": 850,
        "success": true
      }
    },
    "f5_tts_russian": {
      "path": "/path/to/dub_0000_f5.wav",
      "metrics": {
        "provider": "f5_tts_russian",
        "duration_ms": 1200,
        "success": true
      }
    },
    "comparison": {
      "fastest": "aws",
      "latency_diff_ms": 350
    },
    "selected": "aws"
  }
}
```

## Accent Control (Advanced)

F5-TTS supports accent control for better pronunciation. Use `+` before the stressed vowel:

```python
# Without accent mark
text = "молоко"

# With accent mark (молокó)
text = "молок+о"
```

For automatic accent placement, you can use the [RUAccent](https://github.com/Misha24-10/RUAccent) model.

## Performance Optimization

### ONNX Conversion (Future Enhancement)

While F5-TTS doesn't currently have built-in ONNX support, you can:

1. **Use ONNX Runtime for inference** (if model supports it)
2. **Optimize with quantization** (reduce model size)
3. **Use GPU acceleration** (if available)

### Model Selection

- **F5TTS_v1_Base**: Fastest, good for real-time
- **F5TTS_v1_Base_v2**: Best quality, slightly slower
- **F5TTS_v1_Base_accent_tune**: Best quality with accent marks

## Troubleshooting

### "f5-tts_infer-cli command not found"

```bash
pip install git+https://github.com/SWivid/F5-TTS.git
```

### "Model file not found"

Verify the model directory:
```bash
ls -la ./models/f5-tts-russian/
# Should contain: model_last.safetensors, vocab.txt
```

### "F5-TTS inference failed"

Check the error message in logs. Common issues:
- Model file corruption (re-download)
- Insufficient memory (use smaller model variant)
- Text encoding issues (ensure UTF-8)

### Performance Issues

- **Slow inference**: Consider using GPU or smaller model variant
- **High memory usage**: Use model quantization or smaller variant
- **Timeout errors**: Increase timeout in `tts_providers.py`

## Example: Full Comparison Workflow

```bash
# 1. Set up environment
export TTS_PROVIDER="comparison"
export USE_AWS="1"
export F5_TTS_ENABLED="true"
export F5_TTS_CKPT_DIR="./models/f5-tts-russian"
export AWS_REGION="eu-west-1"

# 2. Start worker
python -m app.worker

# 3. Upload Russian audio file
curl -F "file=@russian_audio.wav" \
  "http://127.0.0.1:8000/v1/dubs?src_lang=ru&tgt_lang=ru&voice=Tatyana"

# 4. Monitor comparison results in logs
# Check worker output for TTS comparison metrics
```

## Performance Benchmarks

### Expected Performance (approximate)

| Provider | Latency (ms) | Quality | Cost |
|----------|-------------|---------|------|
| AWS Polly | 500-1000 | High | Pay per character |
| F5-TTS_RUSSIAN | 800-2000 | Very High | Free (compute cost) |

**Note**: Actual performance depends on:
- Text length
- Hardware (CPU/GPU)
- Model variant
- Network latency (for AWS)

## Integration with Existing Pipeline

The F5-TTS integration is transparent - it works with the existing pipeline:

1. **ASR** (faster-whisper) → Russian text
2. **Translation** (AWS Translate or local) → Translated text
3. **TTS** (AWS Polly or F5-TTS_RUSSIAN) → Audio

The provider selection happens automatically based on `TTS_PROVIDER` environment variable.

## Next Steps

1. **Add quality metrics**: BLEU scores, MOS (Mean Opinion Score)
2. **ONNX optimization**: Convert F5-TTS to ONNX for faster inference
3. **GPU support**: Enable CUDA for F5-TTS if GPU available
4. **Accent automation**: Integrate RUAccent for automatic accent placement
5. **Caching**: Cache F5-TTS results similar to translation caching

## References

- [F5-TTS_RUSSIAN Model](https://huggingface.co/Misha24-10/F5-TTS_RUSSIAN)
- [F5-TTS Repository](https://github.com/SWivid/F5-TTS)
- [RUAccent Model](https://github.com/Misha24-10/RUAccent)
- [Demo and Comparison](https://misha24-10.github.io/Misha24-10/)
