#!/usr/bin/env python3
"""
Setup script for F5-TTS_RUSSIAN model.
Downloads and verifies the model installation.
"""

import os
import sys
import subprocess
from pathlib import Path

def check_f5_tts_installed():
    """Check if F5-TTS is installed."""
    try:
        result = subprocess.run(
            ["f5-tts_infer-cli", "--help"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False

def install_f5_tts():
    """Install F5-TTS from GitHub."""
    print("Installing F5-TTS...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "git+https://github.com/SWivid/F5-TTS.git"],
            check=True
        )
        print("[OK] F5-TTS installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to install F5-TTS: {e}")
        return False

def download_model(model_variant="F5TTS_v1_Base"):
    """Download F5-TTS_RUSSIAN model from HuggingFace (only selected variant)."""
    model_dir = Path("./models/f5-tts-russian")
    model_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Downloading F5-TTS_RUSSIAN model ({model_variant}) to {model_dir}...")
    print("[INFO] This will download ~1.5GB (inference model) or ~5.5GB (full model)")
    print("[INFO] Only downloading selected variant to save space")
    
    try:
        from huggingface_hub import snapshot_download
        
        # Try downloading with allow_patterns first (more efficient)
        print(f"Attempting to download {model_variant} variant...")
        try:
            snapshot_download(
                repo_id="Misha24-10/F5-TTS_RUSSIAN",
                local_dir=str(model_dir),
                allow_patterns=[
                    f"{model_variant}/**",  # All files in the variant subdirectory (recursive)
                    "**/vocab.txt",  # Vocab file anywhere in the repo
                ],
                local_dir_use_symlinks=False
            )
            print("[OK] Model downloaded successfully (selective download)")
            return True
        except Exception as e1:
            print(f"[WARNING] Selective download failed: {e1}")
            print("[INFO] Trying full repository download (will download all variants)...")
            print("[WARNING] This will download ~20GB. Press Ctrl+C to cancel.")
            
            # Fallback: download everything (user can cancel if they want)
            try:
                snapshot_download(
                    repo_id="Misha24-10/F5-TTS_RUSSIAN",
                    local_dir=str(model_dir),
                    local_dir_use_symlinks=False
                )
                print("[OK] Full repository downloaded successfully")
                print("[INFO] You can delete other variants to save space if needed")
                return True
            except KeyboardInterrupt:
                print("\n[INFO] Download cancelled by user")
                return False
            except Exception as e2:
                print(f"[ERROR] Full download also failed: {e2}")
                raise
        
    except ImportError:
        print("[ERROR] huggingface_hub not installed")
        print("Install with: pip install huggingface-hub")
        return False
    except Exception as e:
        print(f"[ERROR] Failed to download model: {e}")
        print("\nAlternative: Download manually using huggingface-cli:")
        print(f"  # Download specific variant (recommended, ~1.5GB):")
        print(f"  huggingface-cli download Misha24-10/F5-TTS_RUSSIAN \\")
        print(f"    --include '{model_variant}/*' \\")
        print(f"    --local-dir ./models/f5-tts-russian")
        print(f"\n  # Or download everything (~20GB):")
        print(f"  huggingface-cli download Misha24-10/F5-TTS_RUSSIAN \\")
        print(f"    --local-dir ./models/f5-tts-russian")
        return False

def verify_model(model_variant="F5TTS_v1_Base"):
    """Verify model files are present."""
    model_dir = Path("./models/f5-tts-russian")
    model_subdir = model_dir / model_variant
    
    print(f"\nVerifying model files for {model_variant}...")
    
    # Check vocab.txt (could be in root, subdirectory, or variant folder)
    vocab_file = None
    vocab_locations = [
        model_dir / "vocab.txt",
        model_subdir / "vocab.txt",
    ]
    
    # Also search recursively
    for vocab_path in model_dir.rglob("vocab.txt"):
        vocab_locations.append(vocab_path)
        break
    
    for loc in vocab_locations:
        if loc.exists():
            vocab_file = loc
            break
    
    vocab_ok = vocab_file is not None
    if vocab_ok:
        size_kb = vocab_file.stat().st_size / 1024
        print(f"  [OK] vocab.txt ({size_kb:.1f} KB) at {vocab_file}")
    else:
        print(f"  [ERROR] vocab.txt not found")
        print(f"       Searched in: {model_dir} and subdirectories")
    
    # Check model file (in subdirectory or root)
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
    
    # Fallback: check root
    if model_file is None:
        for name in possible_names:
            candidate = model_dir / name
            if candidate.exists():
                model_file = candidate
                break
    
    model_ok = model_file is not None and model_file.exists()
    if model_ok:
        size_gb = model_file.stat().st_size / (1024 * 1024 * 1024)
        print(f"  [OK] {model_file.name} ({size_gb:.2f} GB)")
        print(f"       Location: {model_file}")
    else:
        print(f"  [ERROR] Model file not found")
        print(f"       Checked: {model_subdir} and {model_dir}")
        print(f"       Expected one of: {possible_names}")
    
    return vocab_ok and model_ok

def test_inference(model_variant="F5TTS_v1_Base"):
    """Test F5-TTS inference with sample text."""
    print("\nTesting F5-TTS inference...")
    
    model_dir = Path("./models/f5-tts-russian")
    model_subdir = model_dir / model_variant
    
    # Find vocab file (could be in root or subdirectory)
    vocab_file = None
    for loc in [model_dir / "vocab.txt", model_subdir / "vocab.txt"]:
        if loc.exists():
            vocab_file = loc
            break
    
    if vocab_file is None:
        print(f"[ERROR] vocab.txt not found in {model_dir} or {model_subdir}")
        return False
    
    # Find model file
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
    
    # Fallback: check root
    if model_file is None:
        for name in possible_names:
            candidate = model_dir / name
            if candidate.exists():
                model_file = candidate
                break
    
    if model_file is None:
        print(f"[ERROR] Model file not found")
        print(f"Checked: {model_subdir} and {model_dir}")
        if model_subdir.exists():
            print(f"Files in {model_variant}/: {[f.name for f in model_subdir.iterdir()]}")
        return False
    output_dir = Path("./test_output")
    output_dir.mkdir(exist_ok=True)
    
    test_text = "Привет, это тест F5-TTS."
    
    try:
        cmd = [
            "f5-tts_infer-cli",
            "--model", model_variant,
            "--ckpt_file", str(model_file),
            "--vocab_file", str(vocab_file),
            "-t", test_text,
            "-o", str(output_dir),
            "-w", "test.wav",
            "--speed", "1.0",
            "--remove_silence"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120  # Increased timeout for first-time model loading
        )
        
        if result.returncode == 0:
            output_file = output_dir / "test.wav"
            if output_file.exists():
                size_kb = output_file.stat().st_size / 1024
                print(f"[OK] Test inference successful! Output: {output_file} ({size_kb:.1f} KB)")
                return True
            else:
                print("[ERROR] Output file not created")
                return False
        else:
            print(f"[ERROR] Inference failed: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("[ERROR] Inference timed out")
        return False
    except Exception as e:
        print(f"[ERROR] Test failed: {e}")
        return False

def main():
    print("=" * 60)
    print("F5-TTS_RUSSIAN Setup Script")
    print("=" * 60)
    
    # Step 1: Check/Install F5-TTS
    print("\n1. Checking F5-TTS installation...")
    if not check_f5_tts_installed():
        print("[INFO] F5-TTS not found, installing...")
        if not install_f5_tts():
            print("\n[ERROR] Setup failed. Please install manually:")
            print("  pip install git+https://github.com/SWivid/F5-TTS.git")
            sys.exit(1)
    else:
        print("[OK] F5-TTS is installed")
    
    # Step 2: Select model variant
    print("\n2. Selecting model variant...")
    model_variant = os.getenv("F5_TTS_MODEL", "F5TTS_v1_Base")
    print(f"   Using variant: {model_variant}")
    print("   (Set F5_TTS_MODEL env var to change: F5TTS_v1_Base, F5TTS_v1_Base_v2, F5TTS_v1_Base_accent_tune)")
    
    # Step 3: Download model
    print("\n3. Downloading model...")
    if not verify_model(model_variant):
        if not download_model(model_variant):
            print("\n[ERROR] Failed to download model")
            print("Download manually with:")
            print(f"  huggingface-cli download Misha24-10/F5-TTS_RUSSIAN \\")
            print(f"    --include '{model_variant}/*' vocab.txt \\")
            print(f"    --local-dir ./models/f5-tts-russian")
            sys.exit(1)
    else:
        print("[OK] Model files already present")
    
    # Step 4: Verify model
    print("\n4. Verifying model...")
    if not verify_model(model_variant):
        print("[ERROR] Model verification failed")
        sys.exit(1)
    
    # Step 5: Test inference
    print("\n5. Testing inference...")
    if not test_inference(model_variant):
        print("[WARNING] Test inference failed, but setup may still work")
        print("Check model files and try running manually")
    
    print("\n" + "=" * 60)
    print("[OK] F5-TTS_RUSSIAN setup complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Set environment variables:")
    print("     export F5_TTS_ENABLED=true")
    print("     export TTS_PROVIDER=comparison")
    print("     # Options: 'comparison' (both), 'f5_tts_russian' (F5-TTS only), 'aws_polly' (AWS only)")
    print("  2. Start worker:")
    print("     python -m app.worker")
    print("\nSee F5_TTS_SETUP.md for more details")

if __name__ == "__main__":
    main()
