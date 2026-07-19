"""
Pytest configuration and shared fixtures.
"""

import os
import pytest
import pytest_asyncio
import tempfile
import shutil
from pathlib import Path
from typing import AsyncGenerator, Generator

# Set test environment variables before imports
os.environ["USE_AWS"] = "0"  # Disable AWS for tests
os.environ["TTS_PROVIDER"] = "aws"  # Use AWS as default (but won't actually call it)
os.environ["F5_TTS_ENABLED"] = "false"  # Disable F5-TTS
os.environ["REDIS_URL"] = os.getenv("REDIS_URL", "redis://localhost:6379/1")  # Use DB 1 for tests
os.environ["LOG_LEVEL"] = "WARNING"  # Reduce log noise in tests

import redis.asyncio as redis
from app.redis_backend import RedisJobStore, RedisEventBus


# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)


@pytest_asyncio.fixture(scope="function")
async def redis_client() -> AsyncGenerator[redis.Redis, None]:
    """Create a Redis client for testing."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/1")
    
    try:
        client = redis.from_url(redis_url, decode_responses=False)
        # Test connection
        await client.ping()
        
        # Clean test database
        await client.flushdb()
        
        yield client
        
        # Cleanup
        await client.flushdb()
        await client.aclose()
    except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
        pytest.skip(f"Redis not available: {e}")


@pytest_asyncio.fixture(scope="function")
async def job_store(redis_client: redis.Redis) -> RedisJobStore:
    """Create a RedisJobStore instance for testing."""
    return RedisJobStore(redis_client)


@pytest_asyncio.fixture(scope="function")
async def event_bus(redis_client: redis.Redis) -> RedisEventBus:
    """Create a RedisEventBus instance for testing."""
    return RedisEventBus(redis_client)


@pytest.fixture(scope="function")
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files."""
    temp_path = Path(tempfile.mkdtemp(prefix="dub_test_"))
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture(scope="function")
def sample_audio_file(temp_dir: Path) -> Path:
    """Create a minimal test audio file."""
    # Create a minimal WAV file header (44 bytes header + silence)
    # This is a valid WAV file structure
    wav_path = temp_dir / "test_audio.wav"
    
    # Minimal WAV file (RIFF header + minimal data)
    # This creates a valid WAV file that can be processed
    wav_data = (
        b'RIFF'  # ChunkID
        b'\x24\x00\x00\x00'  # ChunkSize (36 bytes)
        b'WAVE'  # Format
        b'fmt '  # Subchunk1ID
        b'\x10\x00\x00\x00'  # Subchunk1Size (16)
        b'\x01\x00'  # AudioFormat (PCM)
        b'\x01\x00'  # NumChannels (mono)
        b'\x44\xac\x00\x00'  # SampleRate (44100)
        b'\x88\x58\x01\x00'  # ByteRate
        b'\x02\x00'  # BlockAlign
        b'\x10\x00'  # BitsPerSample (16)
        b'data'  # Subchunk2ID
        b'\x00\x00\x00\x00'  # Subchunk2Size (0 = no data, but valid structure)
    )
    
    with open(wav_path, 'wb') as f:
        f.write(wav_data)
    
    return wav_path


@pytest.fixture(scope="function")
def test_client():
    """Create a test client for FastAPI."""
    # This will be used in integration tests
    # Import here to avoid circular dependencies
    from app.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)
