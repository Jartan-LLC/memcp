"""Logging configuration — JSON or plain, with log injection protection."""

from __future__ import annotations

import json
import logging
import logging.config
import time
from datetime import UTC, datetime
from typing import Any

from memcp.identity import current_key


class JsonFormatter(logging.Formatter):
    """Single-line JSON log output for Docker/aggregator consumption."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "key": getattr(record, "key", None),
            "space": getattr(record, "space", None),
        }
        audit = getattr(record, "audit", None)
        if audit is not None:
            payload["audit"] = audit
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_PLAIN_FORMAT = "%(asctime)s %(levelname)s %(name)s key=%(key)s space=%(space)s %(message)s"


class PlainFormatter(logging.Formatter):
    """Plain-text formatter that escapes control chars to block log injection."""

    _ESCAPES = str.maketrans(
        {
            **{c: f"\\x{c:02x}" for c in range(0x20) if c != 0x09},
            0x0A: "\\n",
            0x0D: "\\r",
            0x7F: "\\x7f",
        }
    )

    def __init__(self) -> None:
        super().__init__(fmt=_PLAIN_FORMAT, defaults={"key": "-", "space": "-"})

    def formatMessage(self, record: logging.LogRecord) -> str:
        return super().formatMessage(record).translate(self._ESCAPES)


class KeyContextFilter(logging.Filter):
    """Attach the current request's key name and space to every record logged inside one."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            key = current_key()
        except LookupError:
            return True  # no key: startup, /health, a request refused with 401, shutdown
        record.key = key.name
        record.space = key.grant.space
        return True


def setup_logging(*, level: str = "INFO", fmt: str = "json") -> None:
    """Configure stdlib logging. Call once at startup."""
    logging.Formatter.converter = time.gmtime

    formatter_name = "json" if fmt == "json" else "plain"
    uvicorn_config = {
        "level": "WARNING",
        "handlers": ["stdout"],
        "propagate": False,
    }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "key_context": {"()": KeyContextFilter},
            },
            "formatters": {
                "plain": {"()": PlainFormatter},
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": formatter_name,
                    "filters": ["key_context"],
                },
            },
            "loggers": {
                "memcp": {
                    "level": level,
                    "handlers": ["stdout"],
                    "propagate": False,
                },
                # The audit record is a security record: MEMCP_LOG_LEVEL doesn't silence it.
                "memcp.audit": {
                    "level": "INFO",
                    "handlers": ["stdout"],
                    "propagate": False,
                },
                "uvicorn": uvicorn_config,
                "uvicorn.error": uvicorn_config,
                "uvicorn.access": uvicorn_config,
            },
            "root": {
                "level": level,
                "handlers": ["stdout"],
            },
        }
    )
