"""Structured JSON logging. Email bodies are never logged (the events table is the audit trail)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

_BODY_KEYS = {"body", "email_body", "reply_body"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update({k: v for k, v in extra.items() if k not in _BODY_KEYS})
        return json.dumps(payload, default=str)


def setup_logging(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("labreach")
    logger.setLevel(level)
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        handler = logging.FileHandler(log_dir / "labreach.log")
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger
