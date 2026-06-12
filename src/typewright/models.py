"""Pydantic models: the typed shapes of data flowing through TypeWright.

Two audiences are kept separate on purpose (DECISIONS.md D5, D6):

- Internal: ``FunctionMetadata`` is the *rich* description the AST parser produces.
Later phases (contract inference, strategy generation) need the full picture,
including the function's source.
- API-facing: ``AnalyzeRequest`` / ``AnalyzeResponse`` expose only what Phase 1 can
honestly provide. We do NOT include fields for features that don't exist yet
(contract, bugs_found, fix_suggestion) — those arrive in their own phases.
"""

import enum

from pydantic import BaseModel, Field


class ArgKind(str, enum.Enum):
    """How an argument is passed to the function."""

    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    POSITIONAL_ONLY= "positional_only"
    KEYWORD_ONLY = "keyword_only"
    VAR_POSITIONAL = "var_positional"  #the *args parameter
    VAR_KEYWORD = "var_keyword"  #the **kwargs parameter


class Argument(BaseModel):
    """One parameter of a function."""

    name: str
    type_hint: str | None =None
    default: str | None =None
    kind: ArgKind = ArgKind.POSITIONAL_OR_KEYWORD


class FunctionMetadata(BaseModel):
    """Rich, internal representation of a parsed function (produced by the parser)."""

    name: str
    args: list[Argument]
    return_type: str | None =None
    docstring: str | None =None
    is_async: bool =False
    decorators: list[str] = Field(default_factory=list)
    signature: str
    source: str


class Contract(BaseModel):
    """The inferred semantic contract of a function (Phase 2).

    Three lists of plain-language statements: what must be true of the inputs
    (``preconditions``), what the function guarantees about its result
    (``postconditions``), and properties that always hold (``invariants``).
    Produced by the LLM from the function's source; consumed by Phase 3 to
    generate Hypothesis strategies. Shape fixed by the API spec (§7.1, D14).
    """

    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)


class AnalyzedFunction(BaseModel):
    """The lean, API-facing view of a parsed function (Phase 1 response)."""

    name: str
    signature: str
    args: list[Argument]
    return_type: str | None =None
    docstring: str | None =None

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


class AnalyzeResponse(BaseModel):
    """Response of POST /v1/analyze (Phase 1: parsed function only)."""

    analysis_id: str
    function: AnalyzedFunction