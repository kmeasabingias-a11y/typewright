"""Tests for property detection (Phase 2). The LLM is mocked — no live key needed (D20, D23)."""

from types import SimpleNamespace

import pytest

from typewright import inference
from typewright.config import Settings
from typewright.errors import PipelineError
from typewright.models import (
    Argument,
    DetectedProperty,
    FunctionMetadata,
    PropertyClass,
    PropertyDetection,
)


def _meta() -> FunctionMetadata:
    return FunctionMetadata(
        name="add",
        args=[
            Argument(name="a", type_hint="int"),
            Argument(name="b", type_hint="int"),
        ],
        return_type="int",
        signature="add(a: int, b: int) -> int",
        source="def add(a: int, b: int) -> int:\n    return a + b\n",
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


def test_infer_properties_returns_analysis(monkeypatch):
    detected = [
        DetectedProperty(
            property_class=PropertyClass.METAMORPHIC,
            relation="add(a, b) == add(b, a)",
            rationale="commutative",
            confidence=0.8,
        )
    ]
    fake = _FakeCompletions(result=PropertyDetection(properties=detected))
    monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = inference.infer_properties(_meta(), settings)

    assert result.detected == detected
    assert result.input_types == {"a": "int", "b": "int"}  # from the AST, not the LLM
    assert result.return_type == "int"
    assert fake.kwargs["model"] == settings.model_standard  # default tier
    assert fake.kwargs["api_key"] == "test-key"
    assert fake.kwargs["response_model"] is PropertyDetection
    assert fake.kwargs["temperature"] == settings.llm_temperature


def test_infer_properties_uses_requested_tier(monkeypatch):
    fake = _FakeCompletions(result=PropertyDetection())
    monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    inference.infer_properties(_meta(), settings, model_tier="premium")

    assert fake.kwargs["model"] == settings.model_premium


def test_infer_properties_missing_key_raises_pipeline_error(monkeypatch):
    settings = _settings_no_key(monkeypatch)
    with pytest.raises(PipelineError) as exc_info:
        inference.infer_properties(_meta(), settings)
    assert exc_info.value.stage == "property_detection"


def test_infer_properties_wraps_llm_failure(monkeypatch):
    fake = _FakeCompletions(exc=RuntimeError("boom"))
    monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    with pytest.raises(PipelineError) as exc_info:
        inference.infer_properties(_meta(), settings)
    assert exc_info.value.stage == "property_detection"
    assert "boom" in exc_info.value.detail
