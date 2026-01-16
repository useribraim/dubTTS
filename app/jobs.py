from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List


@dataclass
class Job:
    job_id: str
    status: str = "queued"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    upload_path: Optional[str] = None
    output_path: Optional[str] = None
    error: Optional[str] = None
    src_lang: str = "en"
    tgt_lang: str = "es"
    voice: str = "Joanna"  # Polly default
    segments: List[str] = field(default_factory=list)


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}

    def create(self, job: Job) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Job:
        return self._jobs[job_id]

    def update(self, job_id: str, **kwargs) -> None:
        job = self._jobs[job_id]
        for k, v in kwargs.items():
            setattr(job, k, v)
        job.updated_at = datetime.utcnow()

