"""Domain errors for TypeWright, mapped to HTTP status codes at the edge.

Phase 1 (DECISIONS.md D8): every error defined here is a *caller* mistake — bad
input — and maps to 400 Bad Request. Anything that is NOT a ``TypeWrightError``
is treated as an unexpected failure and surfaces as 500. Keeping that split in
the type system lets the HTTP layer pick a status code with a single
``isinstance`` check, instead of inspecting messages or guessing.
"""

class TypeWrightError(Exception):
    """Base class for every expected, caller-facing TypeWright error.

    Raise a subclass when the *caller* handed us something we can't analyze.
    The API layer turns any ``TypeWrightError`` into a 400 with the message as
    the response detail; everything else becomes a 500.
    """


class CodeSyntaxError(TypeWrightError):
    """The submitted ``code`` is not valid Python and could not be parsed."""

    def __init__(self, detail:str) -> None:
        self.detail = detail
        super().__init__(f"Submitted code is not valid Python: {detail}")


class NoFunctionError(TypeWrightError):
    """The code parsed, but holds no top-level function to analyze."""

    def __init__(self) -> None:
        super().__init__("No top-level function found in the submitted code.")


class FunctionNotFoundError(TypeWrightError):
    """A ``function_name`` was requested but no top-level function matches it."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"No top-level function named {name!r} was found.")


class AmbiguousFunctionError(TypeWrightError):
    """Several top-level functions exist and no ``function_name`` was given."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        joined = ", ".join(repr(n) for n in names)
        super().__init__(
            f"Multiple top-level functions found ({joined}); "
            "specify `function_name` to choose one."
        )


class PipelineError(Exception):
    """An internal analysis stage failed — our fault, not the caller's (500).

    Deliberately NOT a ``TypeWrightError``: the caller's input was fine, but a
    stage of the pipeline (e.g. LLM property detection) could not complete.
    ``stage`` names the failing step so the API can include it in the 500
    response (§7.1: "500 ... Response includes failure stage"; D15).
    """

    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(f"Pipeline stage {stage!r} failed: {detail}")


class SandboxTimeoutError(Exception):
    """Test execution exceeded its time budget — mapped to 504 (D42).

    Deliberately neither a ``TypeWrightError`` (the caller's input was fine) nor a
    ``PipelineError`` (no stage failed — the tests ran but did not finish within
    ``max_test_runtime_seconds``). §7.1 maps an exceeded budget to 504, so it gets its
    own type and handler rather than folding into the 400/500 families.
    """

    def __init__(self, budget_seconds: float) -> None:
        self.budget_seconds = budget_seconds
        super().__init__(f"Test execution exceeded the {budget_seconds}s time budget.")


class CostBudgetExceededError(Exception):
    """An analysis hit its LLM-cost ceiling — mapped to 402 (Phase 9, D52).

    Deliberately neither a ``TypeWrightError`` (the caller's input was fine) nor a ``PipelineError``
    (no stage failed — the analysis was simply too expensive). Raised from the cost meter at the LLM
    chokepoint and surfaced as 402 Payment Required, with how much was spent and the ceiling.
    """

    def __init__(self, spent_usd: float, limit_usd: float) -> None:
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"Analysis exceeded the ${limit_usd:.2f} cost budget (spent ${spent_usd:.4f})."
        )


class GitHubError(Exception):
    """A GitHub API call failed (auth, fetch, or comment) — Phase 7.

    Raised by ``github.py`` and handled by the worker, which logs/retries and skips commenting.
    It is NOT request-scoped (the worker runs off a queue, not behind the API), so unlike the
    other errors here it maps to no HTTP status.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"GitHub API error: {detail}")


class RateLimitedError(Exception):
    """A client exceeded its request rate — mapped to 429 (Phase 9, D53).

    Neither a caller-input error (400) nor a broken stage (500): the request was well-formed but
    arrived too soon. Carries ``retry_after`` (seconds) for the 429's Retry-After header.
    """

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded; retry after {retry_after}s.")