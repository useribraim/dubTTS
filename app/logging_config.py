"""
Structured logging configuration for Dub MVP.

Provides JSON-formatted logs with correlation IDs, request tracking,
and performance metrics.
"""

import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict
from contextvars import ContextVar

# Context variable for correlation ID (request tracking)
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id.get() or getattr(record, "correlation_id", ""),
        }

        # Add extra fields
        if hasattr(record, "job_id"):
            log_data["job_id"] = record.job_id
        if hasattr(record, "segment_index"):
            log_data["segment_index"] = record.segment_index
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms
        if hasattr(record, "operation"):
            log_data["operation"] = record.operation
        if hasattr(record, "status_code"):
            log_data["status_code"] = record.status_code
        if hasattr(record, "error_type"):
            log_data["error_type"] = record.error_type
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
        if hasattr(record, "stream_entry_id"):
            log_data["stream_entry_id"] = record.stream_entry_id

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add any extra fields
        for key, value in record.__dict__.items():
            if key not in [
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs", "message",
                "pathname", "process", "processName", "relativeCreated",
                "thread", "threadName", "exc_info", "exc_text", "stack_info",
            ]:
                if not key.startswith("_"):
                    log_data[key] = value

        return json.dumps(log_data)


def setup_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Configure logging for the application."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    root_logger.handlers = []

    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))

    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        # Simple format for development
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)

    root_logger.addHandler(handler)

    # Set log levels for noisy libraries
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)


def set_correlation_id(cid: str) -> None:
    """Set correlation ID for current context."""
    correlation_id.set(cid)


def get_correlation_id() -> str:
    """Get current correlation ID."""
    return correlation_id.get() or ""
