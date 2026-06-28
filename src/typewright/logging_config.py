"""Logging setup for TypeWright.

Phase 1 kept this to a single stdlib ``basicConfig``. Phase 9 (D54) adds an optional JSON formatter
(``log_format=json``) so the structured per-analysis traces (``tracing.py``) are machine-parseable for
a log aggregator; the default stays human-readable text.
"""

import json
import logging

from .config import Settings


class _JsonFormatter(logging.Formatter):
    """Render each record as one JSON object, merging any structured ``trace`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        trace = getattr(record, "trace", None)
        if isinstance(trace, dict):
            payload.update(trace)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    """Configure root logging from settings. Call once at app startup."""
    handler = logging.StreamHandler()
    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    logging.basicConfig(level=settings.log_level.upper(), handlers=[handler])