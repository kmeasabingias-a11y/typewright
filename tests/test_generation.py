"""Tests for strategy generation (Phase 3). The LLM is mocked — no live key needed (D28)."""

from types import SimpleNamespace

import pytest

from typewright import generation
from typewright.config import Settings
from typewright.errors import PipelineError
from typewright.models import (
    Argument,
    FunctionMetadata,
    GeneratedStrategy,
    PropertyAnalysis,
    StrategyPlan,
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


def _analysis() -> PropertyAnalysis:
    return PropertyAnalysis(
        detected=[], input_types={"a": "int", "b": "int"}, return_type="int"
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


def test_generate_strategies_returns_plan(monkeypatch):
    plan = StrategyPlan(
        strategies=[
            GeneratedStrategy(argument="a", strategy="st.integers()", rationale="int", confidence=0.95),
            GeneratedStrategy(argument="b", strategy="st.integers()", rationale="int", confidence=0.95),
        ]
    )
    fake = _FakeCompletions(result=plan)
    monkeypatch.setattr(generation, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = generation.generate_strategies(_meta(), _analysis(), settings)

    assert result is plan
    assert [s.argument for s in result.strategies] == ["a", "b"]
    assert fake.kwargs["model"] == settings.model_standard  # default tier
    assert fake.kwargs["api_key"] == "test-key"
    assert fake.kwargs["response_model"] is StrategyPlan
    # temperature is OMITTED unless explicitly configured — current Claude models reject it (D65)
    assert "temperature" not in fake.kwargs


def test_generate_strategies_uses_requested_tier(monkeypatch):
    fake = _FakeCompletions(result=StrategyPlan())
    monkeypatch.setattr(generation, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    generation.generate_strategies(_meta(), _analysis(), settings, model_tier="premium")

    assert fake.kwargs["model"] == settings.model_premium


def test_generate_strategies_missing_key_raises_pipeline_error(monkeypatch):
    settings = _settings_no_key(monkeypatch)
    with pytest.raises(PipelineError) as exc_info:
        generation.generate_strategies(_meta(), _analysis(), settings)
    assert exc_info.value.stage == "strategy_generation"


def test_generate_strategies_wraps_llm_failure(monkeypatch):
    fake = _FakeCompletions(exc=RuntimeError("boom"))
    monkeypatch.setattr(generation, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    with pytest.raises(PipelineError) as exc_info:
        generation.generate_strategies(_meta(), _analysis(), settings)
    assert exc_info.value.stage == "strategy_generation"
    assert "boom" in exc_info.value.detail