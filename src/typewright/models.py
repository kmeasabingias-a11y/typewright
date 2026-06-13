"""Pydantic models: the typed shapes of data flowing through TypeWright.

Two audiences are kept separate on purpose (DECISIONS.md D5, D6):

- Internal: ``FunctionMetadata`` is the *rich* description the AST parser produces.
Later phases (property detection, strategy generation) need the full picture,
including the function's source.
- API-facing: ``AnalyzeRequest`` / ``AnalyzeResponse`` expose only what a phase can
honestly provide. We do NOT include fields for features that don't exist yet
(bugs_found, fix_suggestion) — those arrive in their own phases.
"""

import enum

from pydantic import BaseModel, Field


class ArgKind(str, enum.Enum):
    """How an argument is passed to the function."""

    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    POSITIONAL_ONLY = "positional_only"
    KEYWORD_ONLY = "keyword_only"
    VAR_POSITIONAL = "var_positional"  # the *args parameter
    VAR_KEYWORD = "var_keyword"  # the **kwargs parameter


class Argument(BaseModel):
    """One parameter of a function."""

    name: str
    type_hint: str | None = None
    default: str | None = None
    kind: ArgKind = ArgKind.POSITIONAL_OR_KEYWORD


class FunctionMetadata(BaseModel):
    """Rich, internal representation of a parsed function (produced by the parser)."""

    name: str
    args: list[Argument]
    return_type: str | None = None
    docstring: str | None = None
    is_async: bool = False
    decorators: list[str] = Field(default_factory=list)
    signature: str
    source: str


class PropertyClass(str, enum.Enum):
    """Well-known property classes a function can satisfy (a function may fit several)."""

    ROUND_TRIP = "round_trip"
    IDEMPOTENCE = "idempotence"
    INVARIANT_PRESERVATION = "invariant_preservation"
    METAMORPHIC = "metamorphic"
    TYPE_POSTCONDITION = "type_postcondition"
    VALUE_POSTCONDITION = "value_postcondition"  # output-value constraint from INTENT only — most powerful, most circular-prone; tightest leash (D26)
    TOTALITY = "totality"  # weakest: crash-only, cannot catch wrong answers


class DetectedProperty(BaseModel):
    """One property class the function appears to satisfy (LLM-detected, Phase 2).

    The LLM recognizes which well-known class fits rather than inventing a bespoke
    spec (D23). ``confidence`` is low when it is guessing rather than recognizing.
    """

    property_class: PropertyClass
    relation: str = Field(
        ...,
        description=(
            "A concrete, TESTABLE relation a later phase turns straight into a "
            "test, e.g. 'parse(format(x)) == x' — not vague prose."
        ),
    )
    companion_function: str | None = Field(
        default=None,
        description="For round_trip: the inverse function's name (e.g. 'format').",
    )
    rationale: str = Field(..., description="Why this property is expected. Brief.")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0–1; low when guessing rather than recognizing.",
    )


class PropertyDetection(BaseModel):
    """The LLM's raw structured output: the property classes it recognized."""

    properties: list[DetectedProperty] = Field(default_factory=list)


class PropertyAnalysis(BaseModel):
    """Phase 2 result: detected properties plus the AST-declared types later phases
    need to build strategies (D23, D24). Replaces the earlier ``Contract`` model.
    """

    detected: list[DetectedProperty] = Field(default_factory=list)
    input_types: dict[str, str | None] = Field(default_factory=dict)
    return_type: str | None = None


class AnalyzedFunction(BaseModel):
    """The lean, API-facing view of a parsed function."""

    name: str
    signature: str
    args: list[Argument]
    return_type: str | None = None
    docstring: str | None = None

    @classmethod
    def from_metadata(cls, meta: FunctionMetadata) -> "AnalyzedFunction":
        """Project the rich internal metadata down to the fields we expose."""
        return cls(
            name=meta.name,
            signature=meta.signature,
            args=meta.args,
            return_type=meta.return_type,
            docstring=meta.docstring,
        )


class AnalyzeRequest(BaseModel):
    """Body of POST /v1/analyze."""

    code: str = Field(..., description="Python source containing the function to analyze.")
    function_name: str | None = Field(
        default=None,
        description=(
            "Which function to analyze. Optional when the source has exactly one "
            "top-level function."
        ),
    )
    model_tier: str | None = Field(
        default=None,
        description=(
            "Model tier for property detection: 'economy', 'standard', or "
            "'premium'. Optional; falls back to the configured default, and an "
            "unknown tier degrades to 'standard' (D17). Other spec'd request "
            "fields (include_fix_suggestion, max_test_runtime_seconds) arrive "
            "with the phases that use them (D22)."
        ),
    )


class AnalyzeResponse(BaseModel):
    """Response of POST /v1/analyze.

    Phase 2: the parsed ``function`` plus the ``properties`` it appears to satisfy
    — the well-known property classes the LLM recognized (D23). The later fields
    the API spec promises (``bugs_found``, ``fix_suggestion``, ``metadata``)
    appear only once their phases make them real (D5).
    """

    analysis_id: str
    function: AnalyzedFunction
    properties: PropertyAnalysis
