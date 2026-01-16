import os
import json
import uuid
import asyncio
import time
import traceback
from datetime import datetime

import aiofiles
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Form, Response
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
import redis.asyncio as redis

from app.schemas import CreateDubResponse, DubStatusResponse
from app.redis_backend import (
    REDIS_URL,
    RedisJobStore,
    RedisEventBus,
    events_stream_key,
)
from app.logging_config import setup_logging, get_logger, set_correlation_id
from app.metrics import get_prometheus_metrics
from prometheus_client import CONTENT_TYPE_LATEST

# Setup logging
setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), json_output=os.getenv("LOG_JSON", "0") == "1")
logger = get_logger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
OUTPUT_DIR = os.path.join(DATA_DIR, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="Dub MVP", version="1.0")

r = redis.from_url(REDIS_URL, decode_responses=False)
store = RedisJobStore(r)
bus = RedisEventBus(r)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler to catch all unhandled exceptions."""
    error_type = type(exc).__name__
    error_msg = str(exc)
    error_traceback = traceback.format_exc()
    
    logger.error(
        "Unhandled exception",
        extra={
            "error_type": error_type,
            "error": error_msg,
            "path": request.url.path,
            "method": request.method,
            "operation": "global_exception_handler",
        },
        exc_info=True
    )
    
    # 
    detail = f"{error_type}: {error_msg}"
    if os.getenv("LOG_LEVEL", "INFO") == "DEBUG":
        detail += f"\n\nTraceback:\n{error_traceback}"
    
    return JSONResponse(
        status_code=500,
        content={
            "detail": detail,
            "error_type": error_type,
        }
    )


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Add correlation ID and request logging."""
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
    correlation_id_val = request.headers.get("X-Correlation-ID", request_id)
    set_correlation_id(correlation_id_val)
    
    start_time = time.time()
    
    logger.info(
        "Request started",
        extra={
            "method": request.method,
            "path": request.url.path,
            "correlation_id": correlation_id_val,
            "request_id": request_id,
            "operation": "http_request",
        }
    )
    
    response = await call_next(request)
    
    duration_ms = int((time.time() - start_time) * 1000)
    
    logger.info(
        "Request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "correlation_id": correlation_id_val,
            "request_id": request_id,
            "operation": "http_request",
        }
    )
    
    response.headers["X-Correlation-ID"] = correlation_id_val
    response.headers["X-Request-ID"] = request_id
    return response

@app.post("/v1/dubs", response_model=CreateDubResponse)
async def create_dub(
    file: UploadFile = File(...),
    src_lang: str = Form("en", description="Source language code"),
    tgt_lang: str = Form("ru", description="Target language code"),
    voice: str = Form("Tatyana", description="Voice identifier"),
):
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    
    job_id = uuid.uuid4().hex
    # Sanitize filename to avoid path issues
    safe_filename = file.filename.replace("/", "_").replace("\\", "_")
    upload_path = os.path.join(UPLOAD_DIR, f"{job_id}_{safe_filename}")

    logger.info(
        "Creating job",
        extra={
            "job_id": job_id,
            "file_name": file.filename,  # Changed from "filename" to avoid LogRecord conflict
            "src_lang": src_lang,
            "tgt_lang": tgt_lang,
            "voice": voice,
            "operation": "create_job",
        }
    )

    try:
        # Check Redis connection first
        await r.ping()
    except Exception as e:
        logger.error("Redis connection failed", extra={"error": str(e), "operation": "create_job"})
        raise HTTPException(
            status_code=503,
            detail=f"Redis connection failed: {str(e)}. Make sure Redis is running."
        )

    try:
        async with aiofiles.open(upload_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                await f.write(chunk)

        await store.create_job(job_id, {
            "upload_path": upload_path,
            "src_lang": src_lang,
            "tgt_lang": tgt_lang,
            "voice": voice,
            "status": "queued",
        })

        stream_entry_id = await store.enqueue(job_id)
        await bus.publish(job_id, "status", {"status": "queued"})

        logger.info(
            "Job enqueued",
            extra={"job_id": job_id, "stream_entry_id": stream_entry_id, "operation": "enqueue_job"},
        )

        return CreateDubResponse(job_id=job_id)
    
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.error(
            "Error creating job",
            extra={
                "job_id": job_id,
                "error_type": error_type,
                "error": error_msg,
                "operation": "create_job"
            },
            exc_info=True
        )
        # Clean up uploaded file if it exists
        if os.path.exists(upload_path):
            try:
                os.remove(upload_path)
            except:
                pass
        # Let the global exception handler catch it, or raise HTTPException
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create job: {error_type}: {error_msg}"
        )

@app.get("/v1/dubs/{job_id}", response_model=DubStatusResponse)
async def get_status(job_id: str):
    try:
        job = await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")

    return DubStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        created_at=datetime.fromisoformat(job["created_at"]),
        updated_at=datetime.fromisoformat(job["updated_at"]),
        error=job.get("error") or None,
    )

@app.get("/v1/dubs/{job_id}/events")
async def stream_events(job_id: str, request: Request):
    # Verify job exists
    try:
        await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_gen():
        stream_key = events_stream_key(job_id)
        last_event_id = request.headers.get("Last-Event-ID")

        # Send initial connection status
        yield "event: status\ndata: " + json.dumps({"status": "connected"}) + "\n\n"

        # Replay missed events if Last-Event-ID provided
        if last_event_id:
            try:
                replay = await r.xrange(stream_key, min=f"({last_event_id}", max="+")
                for entry_id, fields in replay:
                    event_type = (fields.get(b"type") or b"").decode()
                    data_raw = (fields.get(b"data") or b"{}").decode()
                    data = json.loads(data_raw) if data_raw else {}
                    yield f"id: {entry_id.decode()}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"
                if replay:
                    last_event_id = replay[-1][0].decode()
            except Exception:
                pass

        if not last_event_id:
            last_event_id = "$"

        while True:
            messages = await r.xread({stream_key: last_event_id}, count=10, block=10000)
            if not messages:
                yield "event: heartbeat\ndata: {}\n\n"
                continue
            _stream, entries = messages[0]
            for entry_id, fields in entries:
                event_type = (fields.get(b"type") or b"").decode()
                data_raw = (fields.get(b"data") or b"{}").decode()
                data = json.loads(data_raw) if data_raw else {}
                entry_id_str = entry_id.decode()
                yield f"id: {entry_id_str}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"
                last_event_id = entry_id_str

    return StreamingResponse(event_gen(), media_type="text/event-stream")

@app.get("/v1/dubs/{job_id}/result")
async def get_result(job_id: str):
    try:
        job = await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")

    if job["status"] != "done" or not job.get("output_path"):
        raise HTTPException(status_code=409, detail="result not ready")

    return FileResponse(job["output_path"], media_type="audio/wav", filename=os.path.basename(job["output_path"]))


@app.get("/v1/dubs/{job_id}/segments/{segment_index}")
async def get_segment(job_id: str, segment_index: int):
    try:
        path = await store.get_segment(job_id, segment_index)
    except KeyError:
        raise HTTPException(status_code=404, detail="segment not found")
    return FileResponse(path, media_type="audio/wav", filename=os.path.basename(path))

@app.get("/")
async def root():
    return {"message": "Dub MVP API", "version": "1.0"}

@app.get("/health")
async def health():
    """Health check endpoint with Redis connectivity check."""
    try:
        # Quick Redis ping
        await r.ping()
        return {"status": "healthy", "redis": "connected"}
    except Exception as e:
        logger.warning("Health check failed", extra={"error": str(e), "operation": "health_check"})
        return {"status": "degraded", "redis": "disconnected"}


@app.get("/v1/metrics")
async def get_metrics():
    """
    Get performance metrics including p50, p95, p99 latencies and cache statistics.
    
    Returns:
        Performance report with latency percentiles and cache improvement metrics
    """
    try:
        from app.metrics import get_performance_report
        report = await get_performance_report()
        return report
    except Exception as e:
        logger.error("Error generating metrics report", extra={"error": str(e), "operation": "get_metrics"}, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate metrics: {str(e)}")


@app.get("/metrics")
async def prometheus_metrics():
    return Response(content=get_prometheus_metrics(), media_type=CONTENT_TYPE_LATEST)
