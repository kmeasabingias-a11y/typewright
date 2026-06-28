"""Shared LLM plumbing: the Instructor-wrapped LiteLLM client every analysis step uses,
plus the one structured-completion call shape they all repeat.

Lifted out of ``inference.py`` once a second LLM caller (Phase 3 strategy generation)
appeared — the split DECISIONS.md D18 deferred to "the Phase 3 trigger" (D27). With a
*third* caller (Phase 4 test generation, D31) the call shape itself — the ``create(...)``
kwargs plus the ``PipelineError`` wrapping each stage repeated verbatim — is hoisted here
too, into ``complete()``. ``inference.py``, ``generation.py``, and ``testgen.py`` each keep a
thin ``_client()`` so tests can still stub the client per-module; they hand that factory to
``complete()``, which preserves the seam (D31).
"""

from __future__ import annotations

from typing import Callable, TypeVar

import instructor
from litellm import completion
from pydantic import BaseModel

from .config import Settings
from .errors import CostBudgetExceededError, PipelineError
from .metrics import add_cost

T = TypeVar("T", bound=BaseModel)


def build_client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client (D13/D17)."""
    return instructor.from_litellm(completion)


def complete(
    client_factory: Callable[[], instructor.Instructor],
    *,
    stage: str,
    settings: Settings,
    model: str,
    response_model: type[T],
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
) -> T:
    """Run one structured Instructor completion, the way every analysis stage does (D31).

    ``client_factory`` is the calling module's ``_client`` (passed, NOT called), so a test
    that monkeypatches ``inference._client`` / ``generation._client`` / ``testgen._client``
    still controls the client — the per-module test seam is preserved.

    The API-key check runs first (so we never build a client without a key — the same order
    the three modules used before the hoist), then the call. ``max_retries`` / ``max_tokens``
    / ``temperature`` / ``timeout`` come from ``settings`` because all callers used the same
    values. Any failure of this stage becomes a ``PipelineError(stage, ...)`` -> HTTP 500
    (D15).
    """
    if not settings.anthropic_api_key:
        raise PipelineError(stage, "no LLM API key configured")
    kwargs = dict(
        model=model,
        response_model=response_model,
        api_key=settings.anthropic_api_key,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens if max_tokens is None else max_tokens,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        messages=messages,
    )
    try:
        completions = client_factory().chat.completions
        # A real Instructor client exposes create_with_completion -> we get the raw response too
        # and bill its cost (add_cost is a no-op outside an analysis cost_scope). Hand-written test
        # fakes only have create(), so fall back to it (those paths make no real LLM call to bill).
        if hasattr(completions, "create_with_completion"):
            parsed, raw = completions.create_with_completion(**kwargs)
            add_cost(raw)
            return parsed
        return completions.create(**kwargs)
    except (PipelineError, CostBudgetExceededError):
        raise
    except Exception as exc:  # noqa: BLE001 — any LLM/transport failure becomes a 500
        raise PipelineError(stage, str(exc)) from exc