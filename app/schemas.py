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
