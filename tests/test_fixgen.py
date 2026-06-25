"""Tests for fix suggestion (Phase 6). The LLM is mocked — no live key needed (D31/D44)."""

from types import SimpleNamespace

import pytest

from typewright import fixgen
from typewright.config import Settings
from typewright.errors import PipelineError
from typewright.models import (
    Argument,
    Bug,
    BugReport,
    BugSeverity,
    FixSuggestion,
    FunctionMetadata,
    GeneratedTestFile,
    ProposedFix,
)

# The buggy function, exactly as testgen embeds it (meta.source.strip()).
_BUGGY_SOURCE = "def first_word(text: str) -> str:\n    return text.split()[0]"
_FIXED_SOURCE = (
    "def first_word(text: str) -> str:\n"
    "    words = text.split()\n"
    '    return words[0] if words else ""'
)
_TEST_FN = "@given(text=st.text())\ndef test_totality(text):\n    first_word(text)"


def _meta() -> FunctionMetadata:
    return FunctionMetadata(
        name="first_word",
        args=[Argument(name="text", type_hint="str")],
        return_type="str",
        signature="first_word(text: str) -> str",
        source=_BUGGY_SOURCE + "\n",
    )


def _test_file() -> GeneratedTestFile:
    source = (
        "\n\n\n".join(
            [
                "from hypothesis import given, strategies as st\nimport pytest",
                _BUGGY_SOURCE,
                _TEST_FN,
            ]
        )
        + "\n"
    )
    return GeneratedTestFile(source=source, test_names=["test_totality"], skipped=[])


def _report(bugs=None) -> BugReport:
    return BugReport(
        bugs=bugs
        if bugs is not None
        else [
            Bug(
                test_name="test_totality",
                failing_input="text=''",
                error="IndexError",
                violated_property="first_word(text) does not raise",
                severity=BugSeverity.CRASH,
            )
        ],
        exit_code=1,
        tests_passed=0,
        tests_failed=1,
    )


class _FakeCompletions:
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
    return Settings(_env_file=None)


def _settings_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TYPEWRIGHT_ANTHROPIC_API_KEY", raising=False)
    return Settings(_env_file=None)


# --- suggest_fix (the LLM call) ---

def test_suggest_fix_returns_proposed_fix(monkeypatch):
    proposed = ProposedFix(corrected_source=_FIXED_SOURCE, explanation="guard empty input")
    fake = _FakeCompletions(result=proposed)
    monkeypatch.setattr(fixgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = fixgen.suggest_fix(_meta(), _report(), settings)

    assert isinstance(result, ProposedFix)
    assert result.corrected_source == _FIXED_SOURCE
    assert fake.kwargs["response_model"] is ProposedFix
    assert fake.kwargs["model"] == settings.model_standard
    user = fake.kwargs["messages"][-1]["content"]
    assert "text=''" in user  # failing input reaches the model
    assert "first_word(text) does not raise" in user  # violated relation reaches the model


def test_suggest_fix_uses_requested_tier(monkeypatch):
    fake = _FakeCompletions(result=ProposedFix(corrected_source=_FIXED_SOURCE, explanation="x"))
    monkeypatch.setattr(fixgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    fixgen.suggest_fix(_meta(), _report(), settings, model_tier="premium")

    assert fake.kwargs["model"] == settings.model_premium


def test_suggest_fix_missing_key_raises(monkeypatch):
    settings = _settings_no_key(monkeypatch)
    with pytest.raises(PipelineError) as exc_info:
        fixgen.suggest_fix(_meta(), _report(), settings)
    assert exc_info.value.stage == "fix_suggestion"


def test_suggest_fix_wraps_llm_failure(monkeypatch):
    fake = _FakeCompletions(exc=RuntimeError("boom"))
    monkeypatch.setattr(fixgen, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)
    with pytest.raises(PipelineError) as exc_info:
        fixgen.suggest_fix(_meta(), _report(), settings)
    assert exc_info.value.stage == "fix_suggestion"
    assert "boom" in exc_info.value.detail


# --- build_fix_file (the deterministic swap) ---

def test_build_fix_file_swaps_function_keeps_tests():
    fix = ProposedFix(corrected_source=_FIXED_SOURCE, explanation="x")
    swapped = fixgen.build_fix_file(_test_file(), _meta(), fix)

    assert swapped is not None
    assert 'return words[0] if words else ""' in swapped.source  # corrected body present
    assert "return text.split()[0]" not in swapped.source  # buggy body gone
    assert "def test_totality(text):" in swapped.source  # same test kept
    assert "from hypothesis import given" in swapped.source  # same imports kept
    assert swapped.test_names == ["test_totality"]


def test_build_fix_file_runs_green_against_the_same_test():
    """The fixed function, spliced into the same @given test, actually passes."""
    fix = ProposedFix(corrected_source=_FIXED_SOURCE, explanation="x")
    swapped = fixgen.build_fix_file(_test_file(), _meta(), fix)
    namespace: dict = {}
    exec(compile(swapped.source, "<fixed>", "exec"), namespace)
    namespace["test_totality"]()  # runs Hypothesis; raises if the property still fails


def test_build_fix_file_none_when_corrected_unparseable():
    fix = ProposedFix(corrected_source="def first_word(text:\n    return", explanation="x")
    assert fixgen.build_fix_file(_test_file(), _meta(), fix) is None


def test_build_fix_file_none_when_wrong_function_name():
    fix = ProposedFix(corrected_source="def other():\n    return 1", explanation="x")
    assert fixgen.build_fix_file(_test_file(), _meta(), fix) is None


def test_build_fix_file_none_when_original_absent():
    tf = GeneratedTestFile(source="x = 1\n", test_names=[], skipped=[])
    fix = ProposedFix(corrected_source=_FIXED_SOURCE, explanation="x")
    assert fixgen.build_fix_file(tf, _meta(), fix) is None


# --- finalize (proposal + verification -> FixSuggestion) ---

def test_finalize_verified_when_rerun_green():
    fix = ProposedFix(corrected_source=_FIXED_SOURCE, explanation="guarded empty input")
    green = BugReport(bugs=[], exit_code=0, tests_passed=3, tests_failed=0)
    suggestion = fixgen.finalize(fix, green)

    assert isinstance(suggestion, FixSuggestion)
    assert suggestion.verified is True
    assert suggestion.code == _FIXED_SOURCE
    assert suggestion.tests_passed == 3
    assert "review" in suggestion.disclaimer.lower()


def test_finalize_unverified_when_rerun_still_fails():
    fix = ProposedFix(corrected_source=_FIXED_SOURCE, explanation="x")
    suggestion = fixgen.finalize(fix, _report())  # exit_code 1, one bug
    assert suggestion.verified is False
    assert suggestion.tests_failed == 1


def test_finalize_unverified_when_report_missing():
    fix = ProposedFix(corrected_source=_FIXED_SOURCE, explanation="x")
    suggestion = fixgen.finalize(fix, None)
    assert suggestion.verified is False
    assert suggestion.tests_passed == 0


def test_finalize_unverified_when_timed_out():
    fix = ProposedFix(corrected_source=_FIXED_SOURCE, explanation="x")
    timed = BugReport(bugs=[], timed_out=True, exit_code=0)
    assert fixgen.finalize(fix, timed).verified is False