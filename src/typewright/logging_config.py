"""Logging setup for TypeWright.

Phase 1 keeps this to a single stdlib ``logging.basicConfig`` call. Structured
logging and LLM trace observability (Langfuse) are explicit later-phase concerns
(see DECISIONS.md D10); we do not pull them forward.
"""

import logging

from .config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure root logging from settings. Call once at app startup."""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )