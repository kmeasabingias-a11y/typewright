"""Tests for test-file generation (Phase 4). The LLM is mocked — no live key needed (D31)."""

from types import SimpleNamespace

import pytest

from typewright import testgen
from typewright.config import Settings
from typewright.errors import PipelineError
from typewright.models import (
    Argument,
    FunctionMetadata,
    GeneratedStrategy,
    GeneratedTestFile,
    GeneratedTests,
    PropertyAnalysis,
    StrategyPlan,
)

_METAMORPHIC_TEST = """\
@given(a=st.integers(), b=st.integers())
def test_metamorphic(a, b):
    assert add(a, b) == add(b, a)"""

_BAD_TEST = """\
@given(a=st.integers())
def test_bad(a):
    assert add(a,"""

_UNDEFINED_REF_TEST = """\
@given(a=st.integers())
def test_round_trip(a):
    assert decode(add(a, 0)) == a"""


def _meta() -> FunctionMetadata:
    return FunctionMetadata(
        name="add",
        args=[Argument(name="a", type_hint="int"), Argument(name="b", type_hint="int")],
        return_type="int",
        signature="add(a: int, b: int) -> int",
        source="def add(a: int, b: int) -> int:\n    return a + b\n",
    )


def _analysis() -> PropertyAnalysis:
    return PropertyAnalysis(detected=[], input_types={"a": "int", "b": "int"}, return_type="int")


def _plan(extra_imports=None) -> StrategyPlan:
    return StrategyPlan(
        strategies=[
            GeneratedStrategy(argument="a", strategy="st.integers()", rationale="int", confidence=0.95),
            GeneratedStrategy(argument="b", strategy="st.integers()", rationale="int", confidence=0.95),
        ],
        extra_imports=extra_imports or [],
    )


class _FakeCompletions:
    """Stand-in for client.chat.completions: records kwargs, returns/raises on create()."""

    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.exc is not None:
            raise self.exc
        return self.result


def _fake_client(completions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _settings_with_key(monkeypatch, key="test-key"):
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    return Settings(_env_file=None)  # ignore any real .env so the test is hermetic


def _settings_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TYPEWRIGHT_ANTHROPIC_API_KEY", raising=False)
    return Settings(_env_file=None)


def test_generate_test_file_assembles_runnable_file(monkeypatch):
    tests = GeneratedTests(test_functions=[_METAMORPHIC_TEST])
    fake = _FakeCompletions(result=tests)
    monkeypatch.setattr(testgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = testgen.generate_test_file(_meta(), _analysis(), _plan(), settings)

    assert isinstance(result, GeneratedTestFile)
    assert "from hypothesis import given, strategies as st" in result.source
    assert "def add(a: int, b: int) -> int:" in result.source  # function under test prepended
    assert "def test_metamorphic(a, b):" in result.source
    assert result.test_names == ["test_metamorphic"]  # read off the AST, not the LLM
    assert fake.kwargs["model"] == settings.model_standard  # default tier
    assert fake.kwargs["response_model"] is GeneratedTests
    # temperature is OMITTED unless explicitly configured — current Claude models reject it (D65)
    assert "temperature" not in fake.kwargs
    assert fake.kwargs["max_tokens"] == settings.llm_max_tokens_codegen


def test_generated_file_is_collectable_and_passes(monkeypatch):
    """The exit criterion in miniature: the assembled file parses, defines the function and
    the test, and the @given test actually runs (and passes) against the function."""
    fake = _FakeCompletions(result=GeneratedTests(test_functions=[_METAMORPHIC_TEST]))
    monkeypatch.setattr(testgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = testgen.generate_test_file(_meta(), _analysis(), _plan(), settings)

    namespace: dict = {}
    exec(compile(result.source, "<generated>", "exec"), namespace)
    assert callable(namespace["add"])
    namespace["test_metamorphic"]()  # runs Hypothesis; raises if the property fails


def test_extra_imports_merged_and_deduped(monkeypatch):
    tests = GeneratedTests(test_functions=[_METAMORPHIC_TEST], extra_imports=["import math", "import base64"])
    fake = _FakeCompletions(result=tests)
    monkeypatch.setattr(testgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    source = testgen.generate_test_file(_meta(), _analysis(), _plan(["import base64"]), settings).source

    assert source.count("import base64") == 1  # requested by both a strategy and a test -> once
    assert "import math" in source


def test_skipped_passed_through(monkeypatch):
    tests = GeneratedTests(test_functions=[], skipped=["round_trip: companion not in snippet"])
    fake = _FakeCompletions(result=tests)
    monkeypatch.setattr(testgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = testgen.generate_test_file(_meta(), _analysis(), _plan(), settings)

    assert result.skipped == ["round_trip: companion not in snippet"]
    assert result.test_names == []


def test_generate_test_file_uses_requested_tier(monkeypatch):
    fake = _FakeCompletions(result=GeneratedTests())
    monkeypatch.setattr(testgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    testgen.generate_test_file(_meta(), _analysis(), _plan(), settings, model_tier="premium")

    assert fake.kwargs["model"] == settings.model_premium


def test_generate_test_file_invalid_python_raises(monkeypatch):
    fake = _FakeCompletions(result=GeneratedTests(test_functions=[_BAD_TEST]))
    monkeypatch.setattr(testgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    with pytest.raises(PipelineError) as exc_info:
        testgen.generate_test_file(_meta(), _analysis(), _plan(), settings)
    assert exc_info.value.stage == "test_generation"


def test_generate_test_file_missing_key_raises(monkeypatch):
    settings = _settings_no_key(monkeypatch)
    with pytest.raises(PipelineError) as exc_info:
        testgen.generate_test_file(_meta(), _analysis(), _plan(), settings)
    assert exc_info.value.stage == "test_generation"


def test_generate_test_file_wraps_llm_failure(monkeypatch):
    fake = _FakeCompletions(exc=RuntimeError("boom"))
    monkeypatch.setattr(testgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    with pytest.raises(PipelineError) as exc_info:
        testgen.generate_test_file(_meta(), _analysis(), _plan(), settings)
    assert exc_info.value.stage == "test_generation"
    assert "boom" in exc_info.value.detail


def test_drops_tests_referencing_undefined_names(monkeypatch):
    tests = GeneratedTests(test_functions=[_METAMORPHIC_TEST, _UNDEFINED_REF_TEST])
    fake = _FakeCompletions(result=tests)
    monkeypatch.setattr(testgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = testgen.generate_test_file(_meta(), _analysis(), _plan(), settings)

    assert "test_metamorphic" in result.test_names       # valid test kept
    assert "test_round_trip" not in result.test_names    # undefined-ref test dropped
    assert "decode" not in result.source
    assert any("decode" in note for note in result.skipped)


def test_assemble_reemits_module_imports():
    """Module-level imports from the pasted code are re-emitted into the file, before the fn (D61)."""
    meta = FunctionMetadata(
        name="f",
        args=[Argument(name="x")],
        signature="(x)",
        source="def f(x):\n    return re.sub('a', 'b', x)",
        module_imports=["import re"],
    )
    plan = StrategyPlan(strategies=[], extra_imports=[])
    tests = GeneratedTests(
        test_functions=["@given(x=st.text())\ndef test_x(x):\n    f(x)"],
        extra_imports=[],
        skipped=[],
    )
    out = testgen._assemble(meta, plan, tests)
    assert "import re" in out
    assert out.index("import re") < out.index("def f(x)")