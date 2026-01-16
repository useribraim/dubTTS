#!/usr/bin/env python3
"""
Quick test script to verify the upload endpoint is working.
"""

import requests
import sys

BASE_URL = "http://127.0.0.1:8000"

def test_health():
    """Test the health endpoint."""
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=2)
        print(f"[OK] Health check: {resp.status_code}")
        print(f"   Response: {resp.json()}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] Health check failed: {e}")
        return False

def test_upload():
    """Test uploading a small file."""
    # Create a minimal test file
    test_file_path = "test_audio.wav"
    
    # Check if karpathy.wav exists, use it if available
    import os
    if os.path.exists("karpathy.wav"):
        test_file_path = "karpathy.wav"
        print(f"Using existing file: {test_file_path}")
    else:
        print(f"[WARNING] File {test_file_path} not found. Please provide a WAV file.")
        return False
    
    print(f"\nTesting upload to {BASE_URL}/v1/dubs")
    print(f"   File: {test_file_path}")
    print(f"   Parameters: src_lang=en, tgt_lang=ru, voice=Tatyana")
    
    try:
        with open(test_file_path, "rb") as f:
            files = {"file": (os.path.basename(test_file_path), f, "audio/wav")}
            data = {
                "src_lang": "en",
                "tgt_lang": "ru",
                "voice": "Tatyana"
            }
            
            resp = requests.post(
                f"{BASE_URL}/v1/dubs",
                files=files,
                data=data,
                timeout=30
            )
            
            print(f"\nResponse Status: {resp.status_code}")
            
            if resp.status_code == 200:
                result = resp.json()
                print(f"[OK] Success! Job ID: {result.get('job_id')}")
                return True
            else:
                print(f"[ERROR] Error: {resp.status_code}")
                try:
                    error_detail = resp.json()
                    print(f"   Error Details: {error_detail}")
                except:
                    print(f"   Error Text: {resp.text[:500]}")
                return False
                
    except requests.exceptions.ConnectionError:
        print("[ERROR] Cannot connect to server. Is it running?")
        print("   Start with: uvicorn app.main:app --reload")
        return False
    except Exception as e:
        print(f"[ERROR] Error: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Testing Dub MVP Upload Endpoint")
    print("=" * 60)
    
    if not test_health():
        print("\n[WARNING] Server health check failed. Make sure the server is running.")
        sys.exit(1)
    
    print()
    if test_upload():
        print("\n[OK] Upload test successful!")
    else:
        print("\n[ERROR] Upload test failed. Check the error messages above.")
        sys.exit(1)
