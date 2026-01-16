#!/usr/bin/env python3
"""
Interactive demo script to show how the Dub MVP application works.

This script demonstrates the complete workflow:
1. Upload audio file
2. Create job in Redis
3. Worker processes the job
4. Stream events via SSE
5. Get final result

Run this after starting:
- Redis: docker run --rm -p 6379:6379 redis:7-alpine
- API: uvicorn app.main:app --reload
- Worker: python -m app.worker
"""

import os
import sys
import time
import requests
import json
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"

def print_section(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def print_step(num, description):
    print(f"\n{num}. {description}")
    print("-" * 60)

def check_services():
    """Check if API and Redis are running"""
    print_section("Checking Services")
    
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=2)
        if resp.status_code == 200:
            print("[OK] FastAPI server is running")
        else:
            print("[ERROR] FastAPI server returned error")
            return False
    except requests.exceptions.ConnectionError:
        print("[ERROR] FastAPI server is not running")
        print("   Start it with: uvicorn app.main:app --reload")
        return False
    
    try:
        resp = requests.get(f"{BASE_URL}/", timeout=2)
        print("[OK] API is responding")
    except:
        print("[ERROR] API is not responding")
        return False
    
    return True

def find_test_file():
    """Find a test audio file"""
    uploads_dir = Path(__file__).parent / "data" / "uploads"
    if uploads_dir.exists():
        for file in uploads_dir.glob("*.wav"):
            return str(file)
    
    # Check if there's a file in the current directory
    for ext in [".wav", ".mp3", ".mp4", ".m4a"]:
        for file in Path(".").glob(f"*{ext}"):
            return str(file)
    
    return None

def create_job(audio_file):
    """Step 1: Upload file and create job"""
    print_step("1", "Uploading audio file and creating job")
    
    if not os.path.exists(audio_file):
        print(f"[ERROR] File not found: {audio_file}")
        return None
    
    print(f"Uploading: {audio_file}")
    
    with open(audio_file, "rb") as f:
        files = {"file": (os.path.basename(audio_file), f, "audio/wav")}
        data = {
            "src_lang": "en",
            "tgt_lang": "es",
            "voice": "Joanna"
        }
        
        try:
            resp = requests.post(f"{BASE_URL}/v1/dubs", files=files, data=data, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            job_id = result["job_id"]
            print(f"[OK] Job created: {job_id}")
            return job_id
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Error creating job: {e}")
            return None

def get_job_status(job_id):
    """Step 2: Check job status"""
    print_step("2", "Checking job status")
    
    try:
        resp = requests.get(f"{BASE_URL}/v1/dubs/{job_id}", timeout=5)
        resp.raise_for_status()
        status = resp.json()
        print(f"Job Status: {status['status']}")
        print(f"   Created: {status['created_at']}")
        print(f"   Updated: {status['updated_at']}")
        if status.get('error'):
            print(f"   Error: {status['error']}")
        return status
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Error getting status: {e}")
        return None

def stream_events(job_id, max_events=20):
    """Step 3: Stream events via SSE"""
    print_step("3", "Streaming events (SSE)")
    print("   This shows real-time progress as the worker processes segments...")
    print("   (Press Ctrl+C to stop streaming)\n")
    
    try:
        resp = requests.get(f"{BASE_URL}/v1/dubs/{job_id}/events", stream=True, timeout=None)
        resp.raise_for_status()
        
        event_count = 0
        segments_completed = 0
        
        for line in resp.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                
                if line_str.startswith('event:'):
                    event_type = line_str.split(':', 1)[1].strip()
                elif line_str.startswith('data:'):
                    data_str = line_str.split(':', 1)[1].strip()
                    try:
                        data = json.loads(data_str)
                        
                        if event_type == "status":
                            status = data.get("status", "unknown")
                            print(f"Status: {status}")
                            if status == "done":
                                print("[OK] Job completed!")
                                break
                            elif status == "failed":
                                error = data.get("error", "Unknown error")
                                print(f"[ERROR] Job failed: {error}")
                                break
                        
                        elif event_type == "segment":
                            segments_completed += 1
                            seg_idx = data.get("segment_index", 0)
                            src_text = data.get("src_text", "")[:50]
                            tgt_text = data.get("tgt_text", "")[:50]
                            total_ms = data.get("total_ms", 0)
                            
                            print(f"Segment {seg_idx}:")
                            print(f"   Source: {src_text}...")
                            print(f"   Target: {tgt_text}...")
                            print(f"   Time: {total_ms}ms")
                        
                        elif event_type == "done":
                            output_path = data.get("output_path", "")
                            print(f"[OK] Final output: {output_path}")
                            break
                        
                        event_count += 1
                        if event_count >= max_events:
                            print(f"\n[WARNING] Reached max events ({max_events}), stopping stream")
                            break
                            
                    except json.JSONDecodeError:
                        pass
        
        return True
    except KeyboardInterrupt:
        print("\n\n[INFO] Streaming stopped by user")
        return True
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Error streaming events: {e}")
        return False

def get_result(job_id, output_file="demo_result.wav"):
    """Step 4: Download final result"""
    print_step("4", "Downloading final result")
    
    try:
        resp = requests.get(f"{BASE_URL}/v1/dubs/{job_id}/result", stream=True, timeout=30)
        resp.raise_for_status()
        
        with open(output_file, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        file_size = os.path.getsize(output_file)
        print(f"[OK] Result saved: {output_file} ({file_size:,} bytes)")
        return output_file
    except requests.exceptions.RequestException as e:
        if e.response and e.response.status_code == 409:
            print("[INFO] Result not ready yet (job may still be processing)")
        else:
            print(f"[ERROR] Error getting result: {e}")
        return None

def main():
    print_section("Dub MVP - Application Workflow Demo")
    print("\nThis demo shows how the microservice architecture works:")
    print("  • FastAPI receives upload → creates job in Redis → enqueues")
    print("  • Worker pulls job from queue → processes segments → publishes events")
    print("  • SSE stream shows real-time progress")
    print("  • Final dubbed audio is available for download")
    
    # Check services
    if not check_services():
        print("\n[WARNING] Please start the required services first:")
        print("   1. Redis: docker run --rm -p 6379:6379 redis:7-alpine")
        print("   2. API: uvicorn app.main:app --reload")
        print("   3. Worker: python -m app.worker")
        sys.exit(1)
    
    # Find test file
    test_file = find_test_file()
    if not test_file:
        print("\n[ERROR] No test audio file found!")
        print("   Please provide a path to an audio file:")
        test_file = input("   File path: ").strip()
        if not test_file or not os.path.exists(test_file):
            print("[ERROR] File not found. Exiting.")
            sys.exit(1)
    
    print(f"\nUsing test file: {test_file}")
    
    # Create job
    job_id = create_job(test_file)
    if not job_id:
        print("\n[ERROR] Failed to create job. Exiting.")
        sys.exit(1)
    
    # Wait a moment for job to be picked up
    print("\nWaiting 2 seconds for worker to pick up job...")
    time.sleep(2)
    
    # Check initial status
    status = get_job_status(job_id)
    
    # Stream events
    print("\nTip: The worker processes segments as they're detected (streaming VAD)")
    print("   This means you'll see segment events appear in real-time!")
    input("\n   Press Enter to start streaming events...")
    
    stream_events(job_id)
    
    # Wait a bit for final processing
    print("\nWaiting 3 seconds for final processing...")
    time.sleep(3)
    
    # Get final status
    status = get_job_status(job_id)
    
    # Download result
    if status and status.get("status") == "done":
        result_file = get_result(job_id)
        if result_file:
            print(f"\nDemo complete! Check {result_file} for the dubbed audio.")
    else:
        print("\n[WARNING] Job may still be processing. You can check status later:")
        print(f"   curl {BASE_URL}/v1/dubs/{job_id}")
        print(f"   curl -o result.wav {BASE_URL}/v1/dubs/{job_id}/result")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nDemo interrupted. Goodbye!")
        sys.exit(0)
