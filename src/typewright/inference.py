"""Phase 2: infer a function's semantic contract with an LLM.

``infer_contract`` takes the parser's ``FunctionMetadata`` and returns a validated
``Contract`` (preconditions / postconditions / invariants, D14). The call goes
through LiteLLM (D17), wrapped by Instructor so the model's reply is coerced into
the ``Contract`` schema with reask retries on bad output (D13). Any failure of this
stage becomes a ``PipelineError`` (stage "contract_inference", D15) -> HTTP 500.
"""

from __future__ import annotations

import instructor
from litellm import completion

from .config import Settings, get_settings
from .errors import PipelineError
from .models import Contract, FunctionMetadata

_STAGE = "contract_inference"

_SYSTEM_PROMPT = (
    "You are a program-analysis assistant. Given a single Python function, infer "
    "its semantic contract: properties that should hold for any correct "
    "implementation. Produce three lists of short, plain-English, testable "
    "statements:\n"
    "- preconditions: what must be true of the arguments for a call to be valid.\n"
    "- postconditions: what the function guarantees about its return value, "
    "relative to the inputs.\n"
    "- invariants: properties that always hold (e.g. the inputs are not mutated, "
    "or a relationship between input and output sizes).\n"
    "Base your answer only on the function's name, signature, docstring, and body. "
    "Leave a list empty if the function gives no basis for it. Be specific; do not "
    "restate the code line by line."
)


def _client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client.

    Factored out so tests can monkeypatch it with a fake that returns a known
    ``Contract`` instead of calling a real model.
    """
    return instructor.from_litellm(completion)


def infer_contract(
    meta: FunctionMetadata,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> Contract:
    """Infer the semantic ``Contract`` for one parsed function.

    Raises ``PipelineError`` (stage "contract_inference") if the LLM call fails for
    any reason — the caller's input was fine, so this surfaces as a 500 (D15).
    """
    settings = settings or get_settings()
    model = settings.model_for_tier(model_tier or settings.default_model_tier)

    if not settings.anthropic_api_key:
        raise PipelineError(_STAGE, "no LLM API key configured")

    user_prompt = (
        "Infer the contract for this function.\n\n"
        f"Signature: {meta.signature}\n\n"
        f"Source:\n{meta.source}"
    )

    try:
        return _client().chat.completions.create(
            model=model,
            response_model=Contract,
            api_key=settings.anthropic_api_key,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
    except PipelineError:
        raise
    except Exception as exc:  # noqa: BLE001 — any LLM/transport failure becomes a 500
        raise PipelineError(_STAGE, str(exc)) from exc
