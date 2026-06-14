"""Phase 2: detect which well-known property classes a function satisfies.

``infer_properties`` takes the parser's ``FunctionMetadata`` and returns a
``PropertyAnalysis``: the property classes the function appears to satisfy
(round-trip, idempotence, invariant-preservation, metamorphic, type-postcondition,
value-postcondition, totality — D23/D24/D26), each with a concrete testable relation
and a confidence, plus the function's AST-declared input/return types for later
phases.

The model's job is RECOGNITION, not spec synthesis: it reasons from the name,
signature, type hints, and docstring and prefers recognized classes over invented
per-function specs, signalling uncertainty with low confidence rather than
fabricating (D23). The call goes through LiteLLM (D17), wrapped by Instructor so
the reply is coerced into ``PropertyDetection`` with reask retries (D13), at low
temperature (D25). Any failure of this stage becomes a ``PipelineError`` (stage
"property_detection", D15) -> HTTP 500.
"""

from __future__ import annotations

import instructor

from .config import Settings, get_settings
from .errors import PipelineError
from .llm import build_client
from .models import FunctionMetadata, PropertyAnalysis, PropertyDetection

_STAGE = "property_detection"

_SYSTEM_PROMPT = (
    "You are a property-based-testing assistant. You are given ONE Python function "
    "(name, signature, type hints, docstring, body). Do NOT restate what it does. "
    "Recognize which well-known PROPERTY CLASSES it plausibly satisfies, reasoning "
    "from its name, signature, type hints, and docstring. Prefer recognized classes "
    "over inventing a bespoke spec. If you are guessing rather than recognizing, say "
    "so with LOW confidence — never fabricate a property to seem useful.\n\n"
    "Property classes (a function may fit several):\n"
    "- round_trip: an inverse exists, so one-then-the-other returns the original "
    "(parse/format, encode/decode, serialize/deserialize, compress/decompress). Name "
    "the inverse in companion_function.\n"
    "- idempotence: doing it twice equals doing it once (normalize, sanitize, sort, "
    "dedup).\n"
    "- invariant_preservation: a structural fact of the output must hold vs the input "
    "(a sort keeps the same length and the same multiset of elements).\n"
    "- metamorphic: a relation between changed inputs and outputs without knowing the "
    "exact output (case/whitespace insensitivity, monotonicity, scaling, "
    "commutativity).\n"
    "- type_postcondition: the output matches the declared return type / shape.\n"
    "- value_postcondition: a constraint on the output VALUE that follows from the "
    "function's intent (a tax is >= 0, a discounted price is between 0 and the "
    "original, a probability is in [0,1]). TIGHTEST LEASH: derive the constraint ONLY "
    "from the name, signature, and docstring (what the function is SUPPOSED to "
    "return), NEVER from what the body computes — deriving it from the body is a "
    "circular oracle. If your only basis would be the body's logic, do NOT emit it (or "
    "emit with very low confidence). This is the most powerful class and the most "
    "prone to fabrication, so be strict and prefer low confidence when unsure. Prefer "
    "it over totality.\n"
    "- totality: the function should not raise on inputs in its declared domain. This "
    "is the WEAKEST class (crash-only — it cannot catch wrong answers); only emit it "
    "when nothing stronger applies, and keep its confidence modest.\n\n"
    "For each detected property give: property_class; a concrete, TESTABLE relation a "
    "later phase can turn straight into a test (e.g. 'parse(format(x)) == x', not "
    "'handles input correctly'); companion_function for round_trip; a short rationale; "
    "and a confidence in [0,1]. Return an empty list only if the function genuinely "
    "affords no property."
)

_FEW_SHOT = (
    "Examples.\n\n"
    "Function: def slugify(text: str) -> str  # lowercases, trims, replaces runs of "
    "non-alphanumerics with single hyphens.\n"
    "Detected:\n"
    "- idempotence, relation 'slugify(slugify(s)) == slugify(s)', confidence 0.95, "
    "rationale 'normalizer — re-running changes nothing'.\n"
    "- metamorphic, relation 'slugify(s) == slugify(s.upper())', confidence 0.8, "
    "rationale 'lowercasing makes it case-insensitive'.\n"
    "- type_postcondition, relation 'isinstance(slugify(s), str)', confidence 0.9.\n\n"
    "Function: def to_base64(data: bytes) -> str  (with an inverse from_base64).\n"
    "Detected:\n"
    "- round_trip, relation 'from_base64(to_base64(d)) == d', companion_function "
    "'from_base64', confidence 0.97, rationale 'encode/decode inverse pair'.\n\n"
    "Function: def apply_discount(price: float, pct: float) -> float  # price after a "
    "pct% discount.\n"
    "Detected:\n"
    "- value_postcondition, relation '0 <= apply_discount(price, pct) <= price', "
    "confidence 0.85, rationale 'a discount never raises the price or drops below "
    "zero — from intent, not the body'.\n"
    "- type_postcondition, relation 'isinstance(apply_discount(price, pct), float)', "
    "confidence 0.9.\n"
)


def _client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client.

    Factored out so tests can monkeypatch it with a fake that returns a known
    ``PropertyDetection`` instead of calling a real model.
    """
    return build_client()


def infer_properties(
    meta: FunctionMetadata,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> PropertyAnalysis:
    """Detect the property classes for one parsed function.

    Returns a ``PropertyAnalysis`` (detected properties + the function's AST types).
    Raises ``PipelineError`` (stage "property_detection") if the LLM call fails for
    any reason — the caller's input was fine, so this surfaces as a 500 (D15).
    """
    settings = settings or get_settings()
    model = settings.model_for_tier(model_tier or settings.default_model_tier)

    if not settings.anthropic_api_key:
        raise PipelineError(_STAGE, "no LLM API key configured")

    user_prompt = (
        "Detect the property classes for this function.\n\n"
        f"Signature: {meta.signature}\n"
        f"Docstring: {meta.docstring or '(none)'}\n\n"
        f"Source:\n{meta.source}"
    )

    try:
        detection = _client().chat.completions.create(
            model=model,
            response_model=PropertyDetection,
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
    except Exception as exc:  # noqa: BLE001 — any LLM/transport failure becomes a 500
        raise PipelineError(_STAGE, str(exc)) from exc

    return PropertyAnalysis(
        detected=detection.properties,
        input_types={arg.name: arg.type_hint for arg in meta.args},
        return_type=meta.return_type,
    )
