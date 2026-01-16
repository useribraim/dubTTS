#!/usr/bin/env python3
"""
Cleanup script to remove files from uploads, outputs, and local dub outputs.
Useful for testing and development.
"""

import os
import shutil
from pathlib import Path

# Get the data directory (same as in main.py)
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
ROOT_DIR = SCRIPT_DIR

# Local dub output files created by helper scripts
DUBBED_OUTPUT_PATTERNS = [
    "russian_dub_*.wav",
    "demo_result.wav",
    "result.wav",
]

def cleanup_directory(directory: Path, description: str):
    """Remove all files and subdirectories from a directory."""
    if not directory.exists():
        print(f"[WARNING] {description} directory doesn't exist: {directory}")
        return 0

    count = 0
    total_size = 0
    
    # Count files first
    for item in directory.iterdir():
        if item.is_file():
            count += 1
            total_size += item.stat().st_size
        elif item.is_dir():
            # Count files in subdirectories
            for subitem in item.rglob("*"):
                if subitem.is_file():
                    count += 1
                    total_size += subitem.stat().st_size
    
    if count == 0:
        print(f"[OK] {description} directory is already empty")
        return 0
    
    # Ask for confirmation
    size_mb = total_size / (1024 * 1024)
    print(f"\n{description}:")
    print(f"   Files to delete: {count}")
    print(f"   Total size: {size_mb:.2f} MB")
    
    response = input(f"\n[WARNING] Delete all files in {description}? (yes/no): ").strip().lower()
    
    if response in ['yes', 'y']:
        # Remove all contents
        for item in directory.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        
        print(f"[OK] Cleaned {description}: removed {count} files ({size_mb:.2f} MB)")
        return count
    else:
        print(f"[INFO] Cancelled. No files deleted from {description}")
        return 0

def cleanup_pattern_files(directory: Path, description: str, patterns: list[str]):
    """Remove files matching patterns within a directory."""
    if not directory.exists():
        print(f"[WARNING] {description} directory doesn't exist: {directory}")
        return 0

    matched_files = []
    total_size = 0
    for pattern in patterns:
        for file_path in directory.glob(pattern):
            if file_path.is_file():
                matched_files.append(file_path)
                total_size += file_path.stat().st_size

    if not matched_files:
        print(f"[OK] {description} has no matching files")
        return 0

    size_mb = total_size / (1024 * 1024)
    print(f"\n{description}:")
    print(f"   Files to delete: {len(matched_files)}")
    print(f"   Total size: {size_mb:.2f} MB")
    for file_path in matched_files:
        print(f"   - {file_path.name}")

    response = input(f"\n[WARNING] Delete these files in {description}? (yes/no): ").strip().lower()

    if response in ['yes', 'y']:
        for file_path in matched_files:
            file_path.unlink()
        print(f"[OK] Cleaned {description}: removed {len(matched_files)} files ({size_mb:.2f} MB)")
        return len(matched_files)

    print(f"[INFO] Cancelled. No files deleted from {description}")
    return 0

def main():
    print("=" * 60)
    print("Cleanup Data Directories")
    print("=" * 60)
    print(f"\nThis will clean:")
    print(f"  • Uploads: {UPLOAD_DIR}")
    print(f"  • Outputs: {OUTPUT_DIR}")
    print(f"  • Dubbed outputs: {ROOT_DIR} ({', '.join(DUBBED_OUTPUT_PATTERNS)})")
    print()
    
    upload_count = cleanup_directory(UPLOAD_DIR, "Uploads")
    print()
    output_count = cleanup_directory(OUTPUT_DIR, "Outputs")
    print()
    dubbed_count = cleanup_pattern_files(ROOT_DIR, "Dubbed outputs", DUBBED_OUTPUT_PATTERNS)
    
    print()
    print("=" * 60)
    total = upload_count + output_count + dubbed_count
    if total > 0:
        print(f"[OK] Cleanup complete! Removed {total} files total.")
    else:
        print("[OK] No files were deleted.")
    print("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[INFO] Cancelled by user.")
    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
