"""Tests for bug verification (Phase 10, D60). The LLM is mocked — no live key (D31/D60)."""

from types import SimpleNamespace

import pytest

from typewright import verify
from typewright.config import Settings
from typewright.errors import PipelineError
from typewright.models import (
    Argument,
    Bug,
    BugSeverity,
    BugVerdict,
    DetectedProperty,
    FunctionMetadata,
    PropertyClass,
)

_SOURCE = "def uppercase(s):\n    return s.upper()\n"


def _meta() -> FunctionMetadata:
    return FunctionMetadata(
        name="uppercase",
        args=[Argument(name="s", type_hint="str")],
        signature="uppercase(s)",
        source=_SOURCE,
    )


def _bug() -> Bug:
    return Bug(
        test_name="test_invariant",
        failing_input="s='ß'",
        error="AssertionError",
        violated_property="len(uppercase(s)) == len(s)",
        severity=BugSeverity.PROPERTY_VIOLATION,
    )


def _detected() -> DetectedProperty:
    return DetectedProperty(
        property_class=PropertyClass.INVARIANT_PRESERVATION,
        relation="len(uppercase(s)) == len(s)",
        rationale="uppercasing preserves length",
        confidence=0.85,
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


def test_verify_bug_returns_verdict(monkeypatch):
    verdict = BugVerdict(
        property_is_contractual=False,
        input_in_domain=True,
        reasoning="uppercasing can change length (ß -> SS)",
    )
    fake = _FakeCompletions(result=verdict)
    monkeypatch.setattr(verify, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = verify.verify_bug(_meta(), _detected(), _bug(), settings)

    assert isinstance(result, BugVerdict)
    assert result.is_real is False  # not contractual -> not a real bug
    assert fake.kwargs["response_model"] is BugVerdict
    assert fake.kwargs["model"] == settings.model_standard
    user = fake.kwargs["messages"][-1]["content"]
    assert "uppercase" in user  # function source reaches the judge
    assert "s='ß'" in user  # failing input reaches the judge
    assert "len(uppercase(s)) == len(s)" in user  # violated property reaches the judge


def test_verify_bug_uses_requested_tier(monkeypatch):
    fake = _FakeCompletions(
        result=BugVerdict(property_is_contractual=True, input_in_domain=True, reasoning="x")
    )
    monkeypatch.setattr(verify, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    verify.verify_bug(_meta(), _detected(), _bug(), settings, model_tier="premium")

    assert fake.kwargs["model"] == settings.model_premium


def test_verify_bug_without_detected_property(monkeypatch):
    fake = _FakeCompletions(
        result=BugVerdict(property_is_contractual=True, input_in_domain=True, reasoning="x")
    )
    monkeypatch.setattr(verify, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    verify.verify_bug(_meta(), None, _bug(), settings)

    user = fake.kwargs["messages"][-1]["content"]
    assert "len(uppercase(s)) == len(s)" in user  # falls back to bug.violated_property


def test_verify_bug_no_key_raises_pipeline_error(monkeypatch):
    settings = _settings_no_key(monkeypatch)
    with pytest.raises(PipelineError):
        verify.verify_bug(_meta(), _detected(), _bug(), settings)


def test_is_real_requires_both_axes():
    assert BugVerdict(property_is_contractual=True, input_in_domain=True, reasoning="").is_real
    assert not BugVerdict(property_is_contractual=False, input_in_domain=True, reasoning="").is_real
    assert not BugVerdict(property_is_contractual=True, input_in_domain=False, reasoning="").is_real
