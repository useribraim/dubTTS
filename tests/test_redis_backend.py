"""
Unit tests for Redis backend (job store and event bus).
"""

import pytest
from app.redis_backend import RedisJobStore, RedisEventBus, JOBS_STREAM_KEY


@pytest.mark.asyncio
async def test_job_store_create_and_get(job_store: RedisJobStore):
    """Test creating and retrieving a job."""
    job_id = "test_job_123"
    fields = {
        "upload_path": "/path/to/file.wav",
        "src_lang": "en",
        "tgt_lang": "es",
        "voice": "Joanna",
    }
    
    await job_store.create_job(job_id, fields)
    
    job = await job_store.get_job(job_id)
    
    assert job["job_id"] == job_id
    assert job["status"] == "queued"
    assert job["upload_path"] == "/path/to/file.wav"
    assert job["src_lang"] == "en"
    assert job["tgt_lang"] == "es"
    assert job["voice"] == "Joanna"
    assert "created_at" in job
    assert "updated_at" in job


@pytest.mark.asyncio
async def test_job_store_update(job_store: RedisJobStore):
    """Test updating a job."""
    job_id = "test_job_456"
    await job_store.create_job(job_id, {"src_lang": "en"})
    
    await job_store.update_job(job_id, status="running", output_path="/path/to/output.wav")
    
    job = await job_store.get_job(job_id)
    assert job["status"] == "running"
    assert job["output_path"] == "/path/to/output.wav"


@pytest.mark.asyncio
async def test_job_store_segments(job_store: RedisJobStore):
    """Test segment management."""
    job_id = "test_job_789"
    await job_store.create_job(job_id, {})
    
    # Append segments
    len1 = await job_store.append_segment(job_id, "/path/seg0.wav", 0)
    assert len1 == 1
    
    len2 = await job_store.append_segment(job_id, "/path/seg1.wav", 1)
    assert len2 == 2
    
    # Get segment by index
    seg0 = await job_store.get_segment(job_id, 0)
    assert seg0 == "/path/seg0.wav"
    
    seg1 = await job_store.get_segment(job_id, 1)
    assert seg1 == "/path/seg1.wav"
    
    # List all segments
    segments = await job_store.list_segments(job_id)
    assert len(segments) == 2
    assert segments[0] == "/path/seg0.wav"
    assert segments[1] == "/path/seg1.wav"


@pytest.mark.asyncio
async def test_job_store_enqueue(job_store: RedisJobStore, redis_client):
    """Test job enqueueing."""
    job_id = "test_job_queue"
    await job_store.create_job(job_id, {})
    
    await job_store.enqueue(job_id)
    
    # Check queue
    messages = await redis_client.xrange(JOBS_STREAM_KEY, min="-", max="+")
    assert len(messages) == 1
    entry_id, fields = messages[0]
    assert fields.get(b"job_id").decode() == job_id


@pytest.mark.asyncio
async def test_event_bus_publish(event_bus: RedisEventBus, redis_client):
    """Test event publishing."""
    job_id = "test_job_events"
    event_type = "segment"
    event_data = {"segment_index": 0, "src_text": "Hello", "tgt_text": "Hola"}
    
    # Publish event
    await event_bus.publish(job_id, event_type, event_data)
    
    # Note: In a real scenario, we'd subscribe to the channel to verify
    # For unit tests, we just verify the publish doesn't raise an exception
    # Integration tests will verify the full event flow
