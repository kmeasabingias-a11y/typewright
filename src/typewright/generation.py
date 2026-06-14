"""Phase 3: generate Hypothesis input strategies from detected properties + types.

``generate_strategies`` takes the parser's ``FunctionMetadata`` and the Phase 2
``PropertyAnalysis`` and returns a ``StrategyPlan``: one Hypothesis strategy per argument
(plus any extra imports) that a later phase drops straight into ``@given(...)`` (D24/D29).

Mirrors ``inference.py``: one structured Instructor call (D19) through the shared client
(D27), at low temperature with few-shot (D25). Any failure becomes a ``PipelineError``
(stage "strategy_generation", D15) -> HTTP 500.
"""

from __future__ import annotations

import instructor

from .config import Settings, get_settings
from .errors import PipelineError
from .llm import build_client
from .models import FunctionMetadata, PropertyAnalysis, StrategyPlan

_STAGE = "strategy_generation"

_SYSTEM_PROMPT = (
    "You are a property-based-testing assistant that writes Hypothesis input strategies. "
    "You are given ONE Python function (signature, argument types, docstring) and the "
    "property classes it has been found to satisfy. Produce ONE strategy per argument that "
    "generates VALID inputs — values in the function's intended domain.\n\n"
    "Reason from the declared TYPE first, then tighten ONLY when the name, docstring, or a "
    "detected property clearly implies a narrower domain (a parser needs well-formed text; a "
    "non-negative quantity uses min_value=0). Do NOT over-constrain: when unsure, use the broad "
    "strategy for the type and a lower confidence — an over-narrow strategy hides bugs by never "
    "generating the inputs that trigger them.\n\n"
    "Each strategy must be a Python expression usable directly in @given(arg=<strategy>), using "
    "`st` (from `from hypothesis import strategies as st`). Common mappings: int -> st.integers(); "
    "float -> st.floats(allow_nan=False); str -> st.text(); bytes -> st.binary(); bool -> "
    "st.booleans(); list[int] -> st.lists(st.integers()). If a strategy needs another import "
    "(e.g. base64), list it in extra_imports.\n\n"
    "For each argument give: argument (its name); strategy (the expression); a short rationale; "
    "and a confidence in [0,1]."
)

_FEW_SHOT = (
    "Examples.\n\n"
    "Function: def add(a: int, b: int) -> int  (metamorphic: add(a, b) == add(b, a)).\n"
    "Strategies:\n"
    "- a: st.integers(), confidence 0.95, rationale 'any int is valid'.\n"
    "- b: st.integers(), confidence 0.95, rationale 'any int is valid'.\n\n"
    "Function: def slugify(text: str) -> str  (idempotence; metamorphic case-insensitivity).\n"
    "Strategies:\n"
    "- text: st.text(), confidence 0.9, rationale 'any string is a valid input to a normalizer'.\n\n"
    "Function: def clamp(x: float, lo: float, hi: float) -> float  (value_postcondition "
    "lo <= result <= hi).\n"
    "Strategies:\n"
    "- x: st.floats(allow_nan=False), confidence 0.85, rationale 'any real number'.\n"
    "- lo: st.floats(allow_nan=False), confidence 0.85, rationale 'any real number'.\n"
    "- hi: st.floats(allow_nan=False), confidence 0.85, rationale 'any real number'.\n"
)


def _client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client.

    Factored out so tests can monkeypatch it with a fake that returns a known
    ``StrategyPlan`` instead of calling a real model.
    """
    return build_client()


def generate_strategies(
        meta: FunctionMetadata,
        analysis: PropertyAnalysis,
        settings: Settings | None = None,
        *,
        model_tier: str | None = None,
) -> StrategyPlan:
    """Generate a Hypothesis strategy per argument for one analyzed function.

    Raises ``PipelineError`` (stage "strategy_generation") if the LLM call fails for any
    reason — the caller's input was fine, so this surfaces as a 500 (D15).
    """
    settings = settings or get_settings()
    model = settings.model_for_tier(model_tier or settings.default_model_tier)

    if not settings.anthropic_api_key:
        raise PipelineError(_STAGE, "no LLM API key configured")
    
    detected = ";".join(
        f"{p.property_class.value} [{p.relation}]" for p in analysis.detected
    ) or "(none)"
    types = ", ".join(
        f"{name}: {type_hint or 'unknown'}" for name, type_hint in analysis.input_types.items()
    ) or "(no arguments)"

    user_prompt = (
        "Write a Hypothesis strategy for each argument of this function.\n\n"
        f"Signature: {meta.signature}\n"
        f"Argument types: {types}\n"
        f"Return type: {analysis.return_type or '(none)'}\n"
        f"Docstring: {meta.docstring or '(none)'}\n"
        f"Detected properties: {detected}\n"
    )

    try:
        return _client().chat.completions.create(
            model=model,
            response_model=StrategyPlan,
            api_key=settings.anthropic_api_key,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + _FEW_SHOT},
                {"role": "user", "content": user_prompt},
            ],
        )
    except PipelineError:
        raise
    except Exception as exc:    # noqa : BLE001 - any LLM/transport failure becomes a 500
        raise PipelineError(_STAGE, str(exc)) from exc