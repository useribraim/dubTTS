# Python Version Compatibility Fix

## Problem
Python 3.14 is too new for `onnxruntime` (required by `faster-whisper`). 
Available `onnxruntime` versions only support up to Python 3.12.

## Solution: Use Python 3.11 or 3.12

### Step 1: Check if you have Python 3.11 or 3.12 installed

```bash
python3.11 --version  # Check for 3.11
python3.12 --version  # Check for 3.12
python3.11 -m venv --help  # Verify venv works
```

### Step 2: Install Python 3.12 (if not available)

**On macOS (using Homebrew):**
```bash
brew install python@3.12
```

**On Linux:**
```bash
# Ubuntu/Debian
sudo apt-get install python3.12 python3.12-venv

# Or download from python.org
```

### Step 3: Create a new virtual environment with Python 3.12

```bash
cd /Users/ibraimabduramanov/Documents/informatikBU

# Remove old venv (optional, but recommended)
rm -rf .venv

# Create new venv with Python 3.12
python3.12 -m venv .venv

# Or if python3.12 is not in PATH, use full path:
# /usr/local/bin/python3.12 -m venv .venv
```

### Step 4: Activate the new virtual environment

```bash
source .venv/bin/activate

# Verify Python version
python --version  # Should show Python 3.12.x
```

### Step 5: Install dependencies

```bash
# Upgrade pip first
pip install --upgrade pip

# Install all dependencies
pip install -r requirements.txt
```

### Step 6: Verify installation

```bash
# Test that faster-whisper works
python -c "from faster_whisper import WhisperModel; print('faster-whisper works!')"

# Test that webrtcvad works
python -c "import webrtcvad; print('webrtcvad works!')"
```

### Step 7: Run the application

```bash
cd dub_mvp

# Terminal 1: Redis
docker run --rm -p 6379:6379 redis:7-alpine

# Terminal 2: FastAPI
export REDIS_URL="redis://localhost:6379/0"
uvicorn app.main:app --reload

# Terminal 3: Worker
export REDIS_URL="redis://localhost:6379/0"
export USE_AWS="0"
python -m app.worker
```

## Alternative: Use pyenv to manage Python versions

If you want to easily switch between Python versions:

```bash
# Install pyenv (if not installed)
brew install pyenv  # macOS
# or: curl https://pyenv.run | bash  # Linux

# Install Python 3.12
pyenv install 3.12.7

# Set local Python version for this project
cd /Users/ibraimabduramanov/Documents/informatikBU
pyenv local 3.12.7

# Create venv with pyenv Python
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Quick Check Commands

```bash
# Check current Python version
python --version

# Check which Python is being used
which python

# Check if onnxruntime can be installed
pip install onnxruntime --dry-run 2>&1 | head -5
```

## Notes

- Python 3.11 or 3.12 are recommended for ML/AI projects
- Python 3.14 is very new and many ML libraries don't support it yet
- Once you create the venv with Python 3.12, you can reuse it for all future work
