"""Structured JSON logging with a request ID (working-agreement convention).

One JSON object per line to stdout: machine-parseable from day one, so the
Phase 4 latency analysis can read its own application logs instead of
scraping text. The request ID lives in a ContextVar set by middleware, which
survives async hops within a request without threading an argument through
every call.

Extra fields ride on the standard logging `extra` mechanism under a single
"extra_fields" key — logging.info("search", extra={"extra_fields": {...}}) —
rather than one attribute per field, because stdlib logging silently drops
extras that collide with LogRecord's own attribute names (e.g. "msg").

Alternative rejected: a logging framework (structlog). It is fine software,
but this is ~40 lines of stdlib and one fewer dependency to defend.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    """Route the root logger through the JSON formatter. Idempotent."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
