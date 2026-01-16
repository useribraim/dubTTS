import asyncio
from typing import Dict, Any

class EventBus:
    """
    Per-job asyncio queue. Each job gets its own queue for SSE streaming.
    """
    def __init__(self):
        self._queues: Dict[str, "asyncio.Queue[dict]"] = {}

    def get_queue(self, job_id: str) -> "asyncio.Queue[dict]":
        if job_id not in self._queues:
            self._queues[job_id] = asyncio.Queue()
        return self._queues[job_id]

    async def publish(self, job_id: str, event_type: str, data: Any) -> None:
        q = self.get_queue(job_id)
        await q.put({"type": event_type, "data": data})
