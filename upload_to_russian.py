#!/usr/bin/env python3
"""
Simple script to upload a WAV file and generate Russian voiceover.

Usage:
    python upload_to_russian.py <audio_file.wav>
    
Or specify source language and voice:
    python upload_to_russian.py <audio_file.wav> --src-lang en --voice Tatyana
"""

import sys
import os
import argparse
import requests
import json
import time
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"

# AWS Polly Russian voices
RUSSIAN_VOICES = {
    "Tatyana": "Tatyana",  # Female voice
    "Maxim": "Maxim",      # Male voice
}

def upload_and_translate(audio_file, src_lang="en", voice="Tatyana", wait_for_completion=True):
    """
    Upload a WAV file and translate to Russian.
    
    Args:
        audio_file: Path to the WAV file
        src_lang: Source language code (default: "en")
        voice: Russian voice to use - "Tatyana" (female) or "Maxim" (male)
        wait_for_completion: If True, wait for job to complete and download result
    """
    
    if not Path(audio_file).exists():
        print(f"[ERROR] File not found: {audio_file}")
        return None
    
    print(f"Uploading: {audio_file}")
    print(f"   Source language: {src_lang}")
    print(f"   Target language: ru (Russian)")
    print(f"   Voice: {voice}")
    print()
    
    # Step 1: Upload and create job
    try:
        with open(audio_file, "rb") as f:
            files = {"file": (Path(audio_file).name, f, "audio/wav")}
            data = {
                "src_lang": src_lang,
                "tgt_lang": "ru",  # Russian
                "voice": voice
            }
            
            print("Uploading file and creating job...")
            resp = requests.post(f"{BASE_URL}/v1/dubs", files=files, data=data, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            job_id = result["job_id"]
            print(f"[OK] Job created: {job_id}")
            print()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Error creating job: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Status Code: {e.response.status_code}")
            try:
                error_detail = e.response.json()
                if isinstance(error_detail, dict):
                    detail = error_detail.get("detail", error_detail)
                    error_type = error_detail.get("error_type", "Unknown")
                    print(f"   Error Type: {error_type}")
                    print(f"   Error Details: {detail}")
                else:
                    print(f"   Error Details: {error_detail}")
            except:
                error_text = e.response.text[:1000]  # First 1000 chars
                if error_text:
                    print(f"   Error Response: {error_text}")
        print()
        print("Troubleshooting:")
        print("   1. Check if Redis is running: python check_redis.py")
        print("   2. Check if FastAPI server is running: curl http://127.0.0.1:8000/health")
        print("   3. Check server logs for detailed error messages")
        print("   4. Make sure the file exists and is readable")
        return None
    
    if not wait_for_completion:
        print(f"\nJob is processing. Check status with:")
        print(f"   curl {BASE_URL}/v1/dubs/{job_id}")
        print(f"   curl -o result.wav {BASE_URL}/v1/dubs/{job_id}/result")
        return job_id
    
    # Step 2: Monitor progress
    print("Processing job (this may take a while)...")
    print("   You can monitor progress in real-time with:")
    print(f"   curl -N {BASE_URL}/v1/dubs/{job_id}/events")
    print()
    
    max_wait_time = 600  # 10 minutes
    start_time = time.time()
    check_interval = 2  # Check every 2 seconds
    
    while True:
        try:
            resp = requests.get(f"{BASE_URL}/v1/dubs/{job_id}", timeout=5)
            resp.raise_for_status()
            status = resp.json()
            
            current_status = status['status']
            elapsed = int(time.time() - start_time)
            
            if current_status == "done":
                print(f"[OK] Job completed! (took {elapsed} seconds)")
                break
            elif current_status == "failed":
                error = status.get('error', 'Unknown error')
                print(f"[ERROR] Job failed: {error}")
                return None
            elif current_status in ["queued", "running"]:
                if elapsed % 10 == 0:  # Print every 10 seconds
                    print(f"   Status: {current_status} (elapsed: {elapsed}s)")
            
            if time.time() - start_time > max_wait_time:
                print(f"[WARNING] Timeout after {max_wait_time} seconds")
                print(f"   Job may still be processing. Check status later:")
                print(f"   curl {BASE_URL}/v1/dubs/{job_id}")
                return job_id
            
            time.sleep(check_interval)
            
        except requests.exceptions.RequestException as e:
            print(f"[WARNING] Error checking status: {e}")
            time.sleep(check_interval)
    
    # Step 3: Download result
    print()
    print("Downloading result...")
    output_file = f"russian_dub_{job_id[:8]}.wav"
    
    try:
        resp = requests.get(f"{BASE_URL}/v1/dubs/{job_id}/result", stream=True, timeout=30)
        resp.raise_for_status()
        
        with open(output_file, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        file_size = os.path.getsize(output_file)
        print(f"[OK] Russian voiceover saved: {output_file} ({file_size:,} bytes)")
        return output_file
        
    except requests.exceptions.RequestException as e:
        if hasattr(e, 'response') and e.response and e.response.status_code == 409:
            print("[INFO] Result not ready yet. Try again in a moment:")
            print(f"   curl -o {output_file} {BASE_URL}/v1/dubs/{job_id}/result")
        else:
            print(f"[ERROR] Error downloading result: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Upload WAV file and generate Russian voiceover",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  # Upload English audio, translate to Russian with Tatyana voice
  python upload_to_russian.py audio.wav

  # Use Maxim (male) voice instead
  python upload_to_russian.py audio.wav --voice Maxim

  # Translate from Spanish to Russian
  python upload_to_russian.py audio.wav --src-lang es

  # Create job but don't wait for completion
  python upload_to_russian.py audio.wav --no-wait

Available Russian voices:
  - Tatyana (female, default)
  - Maxim (male)
        """
    )
    
    parser.add_argument("audio_file", help="Path to WAV audio file")
    parser.add_argument("--src-lang", default="en", 
                       help="Source language code (default: en)")
    parser.add_argument("--voice", default="Tatyana", 
                       choices=list(RUSSIAN_VOICES.keys()),
                       help="Russian voice to use (default: Tatyana)")
    parser.add_argument("--no-wait", action="store_true",
                       help="Don't wait for job completion")
    parser.add_argument("--base-url", default=BASE_URL,
                       help=f"API base URL (default: {BASE_URL})")
    
    args = parser.parse_args()
    
    # Check if API is running
    try:
        resp = requests.get(f"{args.base_url}/health", timeout=2)
        if resp.status_code != 200:
            print("[ERROR] API server is not healthy")
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("[ERROR] Cannot connect to API server")
        print(f"   Make sure the server is running at {args.base_url}")
        print("   Start it with: uvicorn app.main:app --reload")
        sys.exit(1)
    
    # Upload and translate
    result = upload_and_translate(
        args.audio_file,
        src_lang=args.src_lang,
        voice=RUSSIAN_VOICES[args.voice],
        wait_for_completion=not args.no_wait
    )
    
    if result:
        print("\nDone!")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
