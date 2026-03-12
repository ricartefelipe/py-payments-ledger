from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from .correlation import get_correlation_id, get_subject, get_tenant_id

SERVICE_NAME = "py-payments-ledger"


def _use_json_format() -> bool:
    log_format = os.getenv("LOG_FORMAT", "").lower()
    app_env = os.getenv("APP_ENV", "local").lower()
    return log_format == "json" or app_env == "production"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
            "tenant_id": get_tenant_id(),
            "service": SERVICE_NAME,
        }
        if get_subject():
            payload["sub"] = get_subject()
        if record.name != "root":
            payload["logger"] = record.name
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Merge extra dict (log.info("msg", extra={...})) and other non-standard record attrs
        _std = {"name", "msg", "args", "created", "filename", "funcName", "levelname", "levelno", "lineno", "module", "msecs", "pathname", "process", "processName", "relativeCreated", "stack_info", "exc_info", "exc_text", "message", "thread", "threadName", "taskName"}
        for k, v in record.__dict__.items():
            if k not in _std and v is not None:
                payload[k] = v
        return json.dumps(payload, ensure_ascii=False)


class HumanReadableFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        cid = get_correlation_id()
        tid = get_tenant_id()
        ctx = ""
        if cid or tid:
            parts = [f"cid={cid}" if cid else "", f"tid={tid}" if tid else ""]
            ctx = " [" + " ".join(p for p in parts if p) + "]"
        base = super().format(record)
        return f"{base}{ctx}"


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    if _use_json_format():
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            HumanReadableFormatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    root.handlers = [handler]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_extra(**kwargs: Any) -> dict[str, Any]:
    return {"extra": kwargs}
