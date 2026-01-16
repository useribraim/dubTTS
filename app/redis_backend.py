import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, List

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def job_key(job_id: str) -> str:
    return f"dub:job:{job_id}"


def seg_paths_key(job_id: str) -> str:
    return f"dub:job:{job_id}:segment_paths"


def seg_meta_key(job_id: str) -> str:
    return f"dub:job:{job_id}:segment_meta"


def events_channel(job_id: str) -> str:
    return f"dub:job:{job_id}:events"


def events_stream_key(job_id: str) -> str:
    return f"dub:job:{job_id}:events_stream"


def heartbeat_key(job_id: str) -> str:
    return f"dub:job:{job_id}:heartbeat"


QUEUE_KEY = "dub:queue"
JOBS_STREAM_KEY = "dub:jobs:stream"
JOBS_GROUP = "dub:jobs:workers"
EVENTS_STREAM_MAXLEN = int(os.getenv("EVENTS_STREAM_MAXLEN", "10000"))


class RedisJobStore:
    def __init__(self, r: redis.Redis):
        self.r = r

    async def create_job(self, job_id: str, fields: Dict[str, Any]) -> None:
        base = {
            "job_id": job_id,
            "status": "queued",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "error": "",
            "upload_path": "",
            "output_path": "",
            "src_lang": "en",
            "tgt_lang": "es",
            "voice": "Joanna",
            "attempts": "0",
            "heartbeat_ts": "",
            "stream_entry_id": "",
        }
        base.update({k: str(v) for k, v in fields.items()})
        await self.r.hset(job_key(job_id), mapping=base)

    async def get_job(self, job_id: str) -> Dict[str, str]:
        data = await self.r.hgetall(job_key(job_id))
        if not data:
            raise KeyError(job_id)
        return {k.decode(): v.decode() for k, v in data.items()}

    async def update_job(self, job_id: str, **kwargs) -> None:
        mapping = {k: str(v) for k, v in kwargs.items()}
        mapping["updated_at"] = _now_iso()
        await self.r.hset(job_key(job_id), mapping=mapping)

    async def append_segment(self, job_id: str, segment_path: str, segment_index: Optional[int] = None) -> int:
        """
        Store segment path by index. If index is not provided, append by length.
        Returns the number of stored segments.
        """
        if segment_index is None:
            current = await self.r.hlen(seg_paths_key(job_id))
            segment_index = int(current)
        await self.r.hset(seg_paths_key(job_id), str(segment_index), segment_path)
        return int(await self.r.hlen(seg_paths_key(job_id)))

    async def get_segment(self, job_id: str, index: int) -> str:
        val = await self.r.hget(seg_paths_key(job_id), str(index))
        if val is None:
            raise KeyError(f"segment {index}")
        return val.decode()

    async def list_segments(self, job_id: str) -> List[str]:
        vals = await self.r.hgetall(seg_paths_key(job_id))
        if not vals:
            return []
        ordered = sorted(((int(k.decode()), v.decode()) for k, v in vals.items()), key=lambda x: x[0])
        return [v for _, v in ordered]

    async def set_segment_meta(self, job_id: str, segment_index: int, meta: Dict[str, Any]) -> None:
        await self.r.hset(seg_meta_key(job_id), str(segment_index), json.dumps(meta))

    async def get_segment_meta(self, job_id: str, segment_index: int) -> Optional[Dict[str, Any]]:
        val = await self.r.hget(seg_meta_key(job_id), str(segment_index))
        if val is None:
            return None
        return json.loads(val.decode())

    async def increment_attempts(self, job_id: str) -> int:
        attempts = await self.r.hincrby(job_key(job_id), "attempts", 1)
        await self.update_job(job_id, attempts=attempts)
        return int(attempts)

    async def update_heartbeat(self, job_id: str, timestamp: str, ttl_seconds: int) -> None:
        await self.r.set(heartbeat_key(job_id), timestamp, ex=ttl_seconds)
        await self.update_job(job_id, heartbeat_ts=timestamp)

    async def enqueue(self, job_id: str) -> str:
        entry_id = await self.r.xadd(JOBS_STREAM_KEY, {"job_id": job_id})
        entry_id_str = entry_id.decode() if isinstance(entry_id, (bytes, bytearray)) else str(entry_id)
        await self.update_job(job_id, stream_entry_id=entry_id_str)
        return entry_id_str


class RedisEventBus:
    def __init__(self, r: redis.Redis):
        self.r = r

    async def publish(self, job_id: str, event_type: str, data: Any) -> str:
        msg = json.dumps({"type": event_type, "data": data})
        await self.r.publish(events_channel(job_id), msg)
        entry_id = await self.r.xadd(
            events_stream_key(job_id),
            {"type": event_type, "data": json.dumps(data)},
            maxlen=EVENTS_STREAM_MAXLEN,
            approximate=True,
        )
        return entry_id


async def get_redis() -> redis.Redis:
    # decode_responses=False keeps bytes; we decode ourselves where needed
    return redis.from_url(REDIS_URL, decode_responses=False)
