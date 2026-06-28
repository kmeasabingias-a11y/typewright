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


class GeneratedStrategy(BaseModel):
    """One Hypothesis strategy for a single function argument (Phase 3, D29).

    ``strategy`` is a Python expression usable directly in ``@given(arg=<strategy>)``,
    e.g. ``"st.integers()"`` or ``"st.text(min_size=1)"``. ``confidence`` is low when
    the model is guessing a domain rather than reading it off the type.
    """

    argument: str = Field(..., description="The parameter this strategy generates values for.")
    strategy: str = Field(
        ...,
        description="A Hypothesis strategy expression, e.g. 'st.integers()' — usable in @given.",
    )
    rationale: str = Field(..., description="Why this strategy fits the argument. Brief.")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description= "0–1; low when guessing the domain rather than reading the type.",
    )


class StrategyPlan(BaseModel):
    """Phase 3 result: one Hypothesis strategy per argument, plus any extra imports.

    The LLM returns this directly (D29). ``extra_imports`` lists anything a strategy needs
    beyond ``from hypothesis import strategies as st`` (e.g. ``"import base64"``).
    """

    strategies: list[GeneratedStrategy] = Field(default_factory=list)
    extra_imports: list[str] = Field(default_factory=list)


class GeneratedTests(BaseModel):
    """The LLM's raw Phase 4 output (hybrid assembly, D32): the per-property test
    functions it wrote, the extra imports they need, and any property it could not make
    executable.

    Only the test functions come from the model; ``testgen.py`` assembles the final file
    around them (import header + the verbatim function under test). Each ``test_functions``
    item is a complete ``@given``-decorated function as source code.
    """

    test_functions: list[str] = Field(
        default_factory=list,
        description=(
            "Each item is a complete @given-decorated pytest function as source code, "
            "asserting ONE detected relation. Do NOT include the function under test or "
            "the hypothesis/pytest imports."
        ),
    )
    extra_imports: list[str] = Field(
        default_factory=list,
        description="Imports the TESTS need beyond hypothesis/pytest, e.g. 'import math'.",
    )
    skipped: list[str] = Field(
        default_factory=list,
        description=(
            "Properties left untested and why, e.g. "
            "'round_trip: companion from_base64 not in the snippet'."
        ),
    )


class GeneratedTestFile(BaseModel):
    """Phase 4 result (D33/D35): a self-contained, syntactically-valid pytest module.

    ``source`` is the complete file (imports + the function under test + the @given tests)
    that runs under pytest as-is. ``test_names`` are read off the parsed AST (not trusted
    from the LLM). ``skipped`` records properties left untested and why — e.g. a round-trip
    whose inverse is not in the snippet (PROJECT_BRIEF §8 risk 3).
    """
    source: str
    test_names: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class BugSeverity(str, enum.Enum):
    """How a falsified property failed (Phase 5, D40)."""

    CRASH = "crash"  # the function raised an uncaught, non-assertion exception
    PROPERTY_VIOLATION = "property_violation"  # an asserted relation failed — a silent wrong answer


class Bug(BaseModel):
    """One falsified property: the test that failed, the input that broke it, and how badly.

    Phase 5 (D40). ``failing_input`` is the argument text from Hypothesis's falsifying
    example (e.g. ``"v=''"`` or ``"x=0, y=3"``); ``error`` is the exception type
    (``"AssertionError"`` for a violated relation, else the crash type, e.g. ``"IndexError"``);
    ``violated_property`` is the detected relation the test was asserting.
    """

    test_name: str
    failing_input: str
    error: str
    violated_property: str
    severity: BugSeverity


class BugReport(BaseModel):
    """Result of running the generated tests in the sandbox (Phase 5): the bugs found plus
    the run's outcome metadata.

    The API surfaces ``bugs`` as ``bugs_found`` (Unit 3); the rest lets the endpoint decide a
    timeout (504 when ``timed_out``) and fill run metadata. A clean run (``exit_code == 0``) or a
    timed-out run yields no bugs.
    """

    bugs: list[Bug] = Field(default_factory=list)
    timed_out: bool = False
    exit_code: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    output_truncated: bool = False


class ProposedFix(BaseModel):
    """The LLM's raw Phase 6 output (D44): a corrected version of the function under test
    plus a one-line explanation.

    Only the corrected SOURCE comes from the model. fixgen never trusts the model's word that
    the fix works — it swaps this function into the SAME generated tests and re-runs them in the
    sandbox (D45). ``corrected_source`` must be a drop-in replacement: same name and signature,
    defining only that function (no imports, no tests).
    """

    corrected_source: str = Field(
        ...,
        description=(
            "The complete corrected function as source code — SAME name and signature as the "
            "original, a drop-in replacement. Define ONLY this function: no imports, no tests, "
            "no commentary."
        ),
    )
    explanation: str = Field(
        ..., description="One sentence: what was wrong and what the fix changes."
    )


class FixSuggestion(BaseModel):
    """Phase 6 result (D44/D45): a corrected function, VERIFIED by re-running the SAME property
    tests against it in the sandbox.

    ``verified`` is True only when that re-run came back green (no bugs, did not time out,
    exit_code 0); a ``verified=False`` suggestion is the brief's "no confident fix" — surfaced,
    but flagged unproven. ``code`` / ``verified`` / ``tests_passed`` / ``tests_failed`` are the
    §5 fields; ``explanation`` and ``disclaimer`` are extra honest fields (real and useful —
    allowed by D5, which forbids only promised-but-unreal fields; cf. D40). The disclaimer carries
    the brief's mandated "AI suggestion — review carefully" label (§3 Step 7).
    """

    code: str
    explanation: str
    verified: bool
    tests_passed: int = 0
    tests_failed: int = 0
    disclaimer: str = "AI suggestion — review carefully before applying."


class FunctionFinding(BaseModel):
    """One changed function's analysis result, for rendering into the PR comment (Phase 7).

    Produced by the worker after running a changed function through the pipeline: the bugs the
    generated tests found, and the optional verified fix. ``fix_suggestion`` is None when no fix
    was attempted (no bugs, or fix generation off/failed).
    """

    function_name: str
    bugs: list[Bug] = Field(default_factory=list)
    fix_suggestion: FixSuggestion | None = None


class PullRequestJob(BaseModel):
    """The unit of work enqueued from a GitHub ``pull_request`` webhook (Phase 7, D47).

    The minimal slice of the event the worker needs to fetch the diff, analyze the changed
    functions, and comment back: which repo, which PR, the head commit (analyzed + used to
    dedupe redeliveries), and the installation whose token authorizes the GitHub API calls.
    """

    repo_full_name: str  # "owner/repo"
    pr_number: int
    head_sha: str
    installation_id: int


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
            "unknown tier degrades to 'standard' (D17). The other spec'd request "
            "field include_fix_suggestion arrives with the phase that uses it (D22)."
        ),
    )
    max_test_runtime_seconds: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Per-run sandbox time budget in seconds for executing the generated tests "
            "(Phase 5). Optional; falls back to the configured default, and Kestrel "
            "clamps any value down to its own ceiling (D41)."
        ),
    )
    include_fix_suggestion: bool = Field(
        default=False,
        description=(
            "When true AND bugs are found, attempt a verified fix suggestion (Phase 6, LLM "
            "call #4 + a second sandbox run to verify). Optional; defaults to false, so fix "
            "generation is opt-in per request (D44) — the field D22 deferred to this phase."
        ),
    )
    max_cost_usd: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Per-analysis LLM-cost ceiling in USD (Phase 9). Optional; falls back to the configured "
            "default and is clamped down to the server's max (a request can only lower it, not raise "
            "it). Crossing it aborts the analysis with 402 (D52)."
        ),
    )


class AnalysisMetadata(BaseModel):
    """Run metadata for one analysis (Phase 9, D5 made real): cost, timing, and test counts.

    ``llm_cost_usd`` is the summed LiteLLM cost of the analysis's LLM calls (0.0 when the price
    map lacks the model). ``hypothesis_examples_tried`` is ``None`` until a Hypothesis-statistics
    hook is added in the sandbox — left honest-null rather than faked (D5/D40).
    """

    analysis_duration_ms: int = 0
    llm_cost_usd: float = 0.0
    tests_generated: int = 0
    tests_run: int = 0
    hypothesis_examples_tried: int | None = None


class AnalyzeResponse(BaseModel):
    """Response of POST /v1/analyze.

    Phase 6: the parsed ``function``, the ``properties`` it appears to satisfy (D23), the
    ``strategy_plan`` (D29, D30), the ``test_file`` (D33, D36), ``bugs_found`` — the failing
    inputs discovered by running that test file in the Kestrel sandbox (D40, D41; empty when
    every property held) — and ``fix_suggestion``: a corrected function verified by re-running
    the SAME tests (D44, D45). ``fix_suggestion`` is ``null`` unless the caller set
    ``include_fix_suggestion`` AND bugs were found; a present suggestion with ``verified=false``
    is the honest "no confident fix". metadata is real as of Phase 9, D51 — was "appears once its phase (9) makes it real (D5)".
    """

    analysis_id: str
    function: AnalyzedFunction
    properties: PropertyAnalysis
    strategy_plan: StrategyPlan
    test_file: GeneratedTestFile
    bugs_found: list[Bug] = Field(default_factory=list)
    fix_suggestion: FixSuggestion | None = None
    metadata: AnalysisMetadata = Field(default_factory=AnalysisMetadata)