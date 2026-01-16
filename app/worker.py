import os
import asyncio
import socket
import uuid
import redis.asyncio as redis
from redis.exceptions import ResponseError
from prometheus_client import start_http_server

from app.redis_backend import (
    REDIS_URL,
    RedisJobStore,
    RedisEventBus,
    JOBS_STREAM_KEY,
    JOBS_GROUP,
)
from app.pipeline_redis import process_job_redis
from app.logging_config import setup_logging, get_logger
from app.metrics import (
    set_queue_depth,
    set_active_jobs,
    increment_retry,
    increment_failure,
)

# Setup logging
setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), json_output=os.getenv("LOG_JSON", "0") == "1")
logger = get_logger(__name__)

OUTPUT_ROOT = os.getenv("OUTPUT_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "outputs"))
CLAIM_IDLE_MS = int(os.getenv("JOB_CLAIM_IDLE_MS", "60000"))
MAX_ATTEMPTS = int(os.getenv("JOB_MAX_ATTEMPTS", "3"))


def _consumer_name() -> str:
    explicit = os.getenv("WORKER_NAME")
    if explicit:
        return explicit
    host = socket.gethostname()
    return f"{host}-{uuid.uuid4().hex[:8]}"


async def _ensure_group(r: redis.Redis) -> None:
    try:
        await r.xgroup_create(JOBS_STREAM_KEY, JOBS_GROUP, id="0-0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def _update_queue_metrics(r: redis.Redis) -> None:
    try:
        groups = await r.xinfo_groups(JOBS_STREAM_KEY)
        group = next((g for g in groups if g.get("name") == JOBS_GROUP), None)
        if group:
            pending = int(group.get("pending", 0))
            lag = group.get("lag")
            if lag is not None:
                depth = pending + int(lag)
            else:
                depth = pending
            set_queue_depth(depth)
    except Exception:
        pass


async def _claim_stale(r: redis.Redis, consumer: str):
    try:
        next_id, entries = await r.xautoclaim(
            JOBS_STREAM_KEY,
            JOBS_GROUP,
            consumer,
            min_idle_time=CLAIM_IDLE_MS,
            start_id="0-0",
            count=1,
        )
        if entries:
            return entries[0]
    except Exception:
        pass
    return None


async def worker_loop():
    r = redis.from_url(REDIS_URL, decode_responses=False)
    store = RedisJobStore(r)
    bus = RedisEventBus(r)
    consumer = _consumer_name()
    await _ensure_group(r)
    metrics_port = int(os.getenv("WORKER_METRICS_PORT", "9108"))
    start_http_server(metrics_port)

    logger.info(
        "Worker started",
        extra={
            "redis_url": REDIS_URL,
            "output_root": OUTPUT_ROOT,
            "queue_stream": JOBS_STREAM_KEY,
            "consumer_group": JOBS_GROUP,
            "consumer": consumer,
            "metrics_port": metrics_port,
            "operation": "worker_start",
        },
    )

    while True:
        try:
            entry = await _claim_stale(r, consumer)
            if entry is None:
                messages = await r.xreadgroup(
                    JOBS_GROUP,
                    consumer,
                    streams={JOBS_STREAM_KEY: ">"},
                    count=1,
                    block=5000,
                )
                if not messages:
                    await _update_queue_metrics(r)
                    continue
                _stream, entries = messages[0]
                entry = entries[0]

            entry_id, fields = entry
            job_id_bytes = fields.get(b"job_id")
            if not job_id_bytes:
                await r.xack(JOBS_STREAM_KEY, JOBS_GROUP, entry_id)
                continue

            job_id = job_id_bytes.decode()
            entry_id_str = entry_id.decode() if isinstance(entry_id, (bytes, bytearray)) else str(entry_id)
            attempts = await store.increment_attempts(job_id)
            logger.info(
                "Job claimed from stream",
                extra={"job_id": job_id, "attempts": attempts, "stream_entry_id": entry_id_str, "operation": "pick_job"},
            )

            if attempts > MAX_ATTEMPTS:
                await store.update_job(job_id, status="failed", error="max_attempts_exceeded")
                increment_failure()
                await r.xack(JOBS_STREAM_KEY, JOBS_GROUP, entry_id)
                continue

            set_active_jobs(1)
            success = await process_job_redis(store, bus, job_id, OUTPUT_ROOT)
            if success:
                await r.xack(JOBS_STREAM_KEY, JOBS_GROUP, entry_id)
                logger.info(
                    "Job processing completed",
                    extra={"job_id": job_id, "stream_entry_id": entry_id_str, "operation": "complete_job"},
                )
            else:
                if attempts >= MAX_ATTEMPTS:
                    increment_failure()
                    await store.update_job(job_id, status="failed", error="max_attempts_exceeded")
                    await r.xack(JOBS_STREAM_KEY, JOBS_GROUP, entry_id)
                else:
                    increment_retry()
        except Exception as e:
            logger.error(
                "Error in worker loop",
                extra={"error_type": type(e).__name__, "error": str(e), "operation": "worker_loop"},
                exc_info=True,
            )
            # Continue processing other jobs
            await asyncio.sleep(1)
        finally:
            set_active_jobs(0)


def main():
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
