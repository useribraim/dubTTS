from pydantic import BaseModel, Field
from typing import Optional, Literal, List
from datetime import datetime

JobStatus = Literal["queued", "running", "done", "failed"]

class CreateDubResponse(BaseModel):
    job_id: str

class DubStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None

class SegmentEvent(BaseModel):
    segment_index: int
    start_ms: int
    end_ms: int
    src_text: str
    tgt_text: str
    audio_path: str
    asr_ms: int = 0
    mt_ms: int = 0
    tts_ms: int = 0

class JobDoneEvent(BaseModel):
    output_path: str


StreamStatus = Literal["open", "running", "finalized", "done", "failed"]


class CreateStreamResponse(BaseModel):
    session_id: str


class StreamStatusResponse(BaseModel):
    session_id: str
    status: str
    src_lang: str
    tgt_lang: str
    voice: str
    finalized: bool
    total_segments: int
    done_segments: int
    created_at: float
    updated_at: float
    error: Optional[str] = None
