"""Tests for the AST parser (``typewright.parser.parse_function``).

Example-based tests cover the Phase 1 exit criteria and the parser's chosen edge
cases (argument kinds, defaults, async, decorators, top-level-only scope, and the
four caller-facing errors). One Hypothesis test asserts the property that the
parser never crashes on valid single-function source (DECISIONS.md D11).
"""

import keyword

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from typewright.errors import (
    AmbiguousFunctionError,
    CodeSyntaxError,
    FunctionNotFoundError,
    NoFunctionError,
)
from typewright.models import ArgKind
from typewright.parser import parse_function


# --- The happy path: the Phase 1 exit criteria ------------------------------


def test_simple_function_metadata():
    source = (
        "def add(a: int, b: int) -> int:\n"
        '    """Add two numbers."""\n'
        "    return a + b\n"
    )
    meta = parse_function(source)

    assert meta.name == "add"
    assert meta.return_type == "int"
    assert meta.docstring == "Add two numbers."
    assert meta.is_async is False
    assert meta.decorators == []
    assert meta.signature == "(a: int, b: int) -> int"
    assert [arg.name for arg in meta.args] == ["a", "b"]
    assert all(arg.type_hint == "int" for arg in meta.args)
    assert "def add" in meta.source


def test_missing_annotations_and_docstring_are_none():
    meta = parse_function("def f(x):\n    return x")

    assert meta.return_type is None
    assert meta.docstring is None
    assert meta.args[0].type_hint is None
    assert meta.signature == "(x)"


# --- Argument kinds and defaults --------------------------------------------


def test_all_argument_kinds_in_order():
    meta = parse_function("def f(p, /, q, *args, r, **kwargs):\n    pass")

    assert [arg.name for arg in meta.args] == ["p", "q", "args", "r", "kwargs"]
    kinds = {arg.name: arg.kind for arg in meta.args}
    assert kinds["p"] == ArgKind.POSITIONAL_ONLY
    assert kinds["q"] == ArgKind.POSITIONAL_OR_KEYWORD
    assert kinds["args"] == ArgKind.VAR_POSITIONAL
    assert kinds["r"] == ArgKind.KEYWORD_ONLY
    assert kinds["kwargs"] == ArgKind.VAR_KEYWORD


def test_defaults_align_to_the_tail():
    meta = parse_function("def f(a, b=1, *, c, d=2):\n    pass")

    defaults = {arg.name: arg.default for arg in meta.args}
    assert defaults["a"] is None        # required positional
    assert defaults["b"] == "1"        # optional positional
    assert defaults["c"] is None        # required keyword-only
    assert defaults["d"] == "2"        # optional keyword-only


# --- async, decorators, and scope -------------------------------------------


def test_async_function_detected():
    meta = parse_function("async def fetch(url: str) -> str:\n    ...")

    assert meta.is_async is True
    assert meta.name == "fetch"


def test_decorators_captured_in_order():
    source = "@staticmethod\n@my.decorator\ndef handler():\n    pass\n"
    meta = parse_function(source)

    assert meta.decorators == ["staticmethod", "my.decorator"]


def test_only_top_level_functions_discovered():
    """Methods and nested functions are invisible (DECISIONS.md D7)."""
    source = (
        "class C:\n"
        "    def method(self):\n"
        "        pass\n"
        "\n"
        "def top():\n"
        "    def inner():\n"
        "        pass\n"
        "    return inner\n"
    )
    # `top` is the only *top-level* function, so no name is needed.
    meta = parse_function(source)
    assert meta.name == "top"


# --- Selecting among several functions --------------------------------------


def test_function_name_selects_among_many():
    source = "def a():\n    pass\n\ndef b(x: str) -> str:\n    return x"
    meta = parse_function(source, function_name="b")

    assert meta.name == "b"
    assert meta.return_type == "str"


# --- The four caller-facing errors ------------------------------------------


def test_syntax_error_raises_code_syntax_error():
    with pytest.raises(CodeSyntaxError):
        parse_function("def f(:\n    pass")


def test_no_function_raises_no_function_error():
    with pytest.raises(NoFunctionError):
        parse_function("x = 1\ny = 2")


def test_ambiguous_without_name_raises():
    with pytest.raises(AmbiguousFunctionError):
        parse_function("def a():\n    pass\n\ndef b():\n    pass")


def test_unknown_name_raises_function_not_found():
    with pytest.raises(FunctionNotFoundError):
        parse_function("def a():\n    pass", function_name="zzz")


# --- The one property-based test (DECISIONS.md D11) -------------------------

# Valid, non-keyword Python identifiers.
_identifiers = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]*", fullmatch=True).filter(
    lambda s: not keyword.iskeyword(s)
)


@settings(deadline=None)
@given(name=_identifiers, params=st.lists(_identifiers, unique=True, max_size=5))
def test_parser_never_crashes_on_valid_function(name, params):
    """For any valid single-function source, the parser returns clean metadata."""
    source = f"def {name}({', '.join(params)}):\n    pass\n"
    meta = parse_function(source)

    assert meta.name == name
    assert [arg.name for arg in meta.args] == params