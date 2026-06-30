"""Phase 10 (D60): a second-opinion verdict on each reported bug — the precision filter.

``verify_bug`` takes a reported ``Bug`` plus the function under test and the detected property it
violated, and returns a ``BugVerdict``: a skeptical judge's call on whether the finding is real.
It does ONLY the LLM call, like ``fixgen.suggest_fix`` — and like that step it's best-effort and
downstream of an already-valid analysis, so the route catches its failures (a verification error
leaves the bug unverified rather than failing the request, D44/D60).

WHY this exists (the bug-hunt eval, 2026-06-30): an automated sweep over 49 real library functions
flagged 15 candidates of which only 3 were real (precision ~20%). The 12 false positives split
cleanly into two classes — (a) the violated property was never part of the function's contract
(``uppercase('ß')`` doesn't preserve length; ``under2camel`` was never idempotent), and (b) the
failing input was out of the function's domain (``flatten_iter(0)`` on a non-iterable). This judge
asks exactly those two questions. It is deliberately NOT confidence-gating, which D57 rejected:
the false metamorphic and the genuine one both scored 0.90, so "does the property hold?" can't
separate them; "is the property contractual and the input in-domain?" can.

Mirrors ``inference``/``generation``/``testgen``/``fixgen``: one structured Instructor call via the
shared ``complete`` helper (D27/D31), low temperature, few-shot grounded in the eval's real cases.
"""

from __future__ import annotations

import instructor

from .config import Settings, get_settings
from .llm import build_client, complete
from .models import Bug, BugVerdict, DetectedProperty, FunctionMetadata

_STAGE = "bug_verification"

_SYSTEM_PROMPT = (
    "You are a STRICT code reviewer auditing an automated bug report. A property-based tester "
    "claims a Python function violates some property on some input. Most such reports are FALSE "
    "ALARMS — the tester routinely over-generalizes (asserting properties the function never "
    "promised) and over-broadens inputs (feeding values outside the function's domain). Your job "
    "is to decide, skeptically, whether the report is a REAL bug.\n\n"
    "Judge exactly two things, independently:\n"
    "1. property_is_contractual — is the violated property genuinely guaranteed by THIS function's "
    "contract (its docstring, name, and signature)? A property is NOT contractual if it is a "
    "plausible-sounding generalization the function never promised: e.g. case-insensitivity, "
    "Unicode length-preservation under upper/lower, idempotence of a non-idempotent transform, or "
    "a metamorphic relation that simply isn't part of what the function does.\n"
    "2. input_in_domain — is the failing input one the function is actually meant to accept? It is "
    "NOT in-domain if it contradicts the parameter's evident type/purpose (e.g. a non-iterable "
    "passed to something that iterates, or an input the docstring excludes), where raising or a "
    "different result is acceptable behavior.\n\n"
    "Default to NOT-a-bug when unsure: only call a property contractual when the docstring/name "
    "clearly back it, and only call an input in-domain when the function is plainly meant to accept "
    "it. Give one or two sentences of reasoning. Do not be swayed by how confident the original "
    "report sounds."
)

_FEW_SHOT = (
    "Examples.\n"
    "A) function `def uppercase(s): return s.upper()`; violated property "
    "`len(uppercase(s)) == len(s)`; failing input s='ß'. => property_is_contractual=false "
    "(uppercasing can change length: 'ß'->'SS'; the function never promised length-preservation), "
    "input_in_domain=true. NOT a real bug (over-inferred property).\n"
    "B) function `def flatten_iter(iterable): ...` (yields leaves of a nested iterable); violated "
    "property `flatten_iter(x) does not raise`; failing input x=0. => property_is_contractual=true, "
    "input_in_domain=false (0 is not an iterable; raising is fine). NOT a real bug (out-of-domain).\n"
    "C) function `def to_text(obj, maxlen): ...` (docstring: caps the string at maxlen); violated "
    "property `len(to_text(obj, maxlen)) <= maxlen`; failing input obj=12345, maxlen=2 -> '1234...'. "
    "=> property_is_contractual=true (it promises a length cap), input_in_domain=true (2 is a valid "
    "maxlen). REAL bug.\n"
    "D) function `def absolute(x): # always >= 0`; violated property `absolute(x) == absolute(-x)`; "
    "failing input x=1. => property_is_contractual=true (symmetry is inherent to absolute value), "
    "input_in_domain=true. REAL bug.\n"
)


def _client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client.

    Factored out so tests can monkeypatch it with a fake that returns a known ``BugVerdict``
    instead of calling a real model. Passed to ``complete`` (D31), which preserves this seam.
    """
    return build_client()


def verify_bug(
    meta: FunctionMetadata,
    detected: DetectedProperty | None,
    bug: Bug,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> BugVerdict:
    """Ask a skeptical judge whether ``bug`` is a real finding (D60).

    ``detected`` is the property the bug violated (so the judge sees the detector's own rationale
    and class); it may be ``None`` if the relation can't be matched back. Does ONLY the LLM call —
    raises ``PipelineError`` (stage "bug_verification") on failure, which the route treats as
    best-effort and catches so a failed verification leaves the bug unverified rather than sinking
    the already-valid ``bugs_found`` (D44/D60).
    """
    settings = settings or get_settings()
    model = settings.model_for_tier(model_tier or settings.default_model_tier)

    if detected is not None:
        property_desc = (
            f"{detected.property_class.value}: {detected.relation}\n"
            f"(the tester's rationale for expecting this: {detected.rationale})"
        )
    else:
        property_desc = bug.violated_property

    user_prompt = (
        "Audit this bug report.\n\n"
        f"Function under test:\n{meta.source}\n\n"
        f"Violated property:\n{property_desc}\n\n"
        f"Failing input: {bug.failing_input or '(none captured)'}\n"
        f"Observed failure: {bug.error} (classified as {bug.severity.value})\n\n"
        "Is the property genuinely contractual for this function, and is the failing input within "
        "its intended domain?"
    )

    return complete(
        _client,
        stage=_STAGE,
        settings=settings,
        model=model,
        response_model=BugVerdict,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + _FEW_SHOT},
            {"role": "user", "content": user_prompt},
        ],
    )
