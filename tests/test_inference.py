"""Tests for contract inference (Phase 2). The LLM is mocked — no live key needed (D20)."""

from types import SimpleNamespace

import pytest

from typewright import inference
from typewright.config import Settings
from typewright.errors import PipelineError
from typewright.models import Contract, FunctionMetadata


def _meta() -> FunctionMetadata:
    return FunctionMetadata(
        name="add",
        args=[],
        signature="add(a: int, b: int) -> int",
        source="def add(a: int, b: int) -> int:\n    return a + b\n",
    )


class _FakeCompletions:
    """Stand-in for client.chat.completions: records kwargs, returns/raises on create()."""

    def __init__(self, result: Contract | None = None, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.exc is not None:
            raise self.exc
        return self.result


def _fake_client(completions: _FakeCompletions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _settings_with_key(monkeypatch, key: str = "test-key") -> Settings:
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    return Settings(_env_file=None)  # ignore any real .env so the test is hermetic


def _settings_no_key(monkeypatch) -> Settings:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TYPEWRIGHT_ANTHROPIC_API_KEY", raising=False)
    return Settings(_env_file=None)


def test_infer_contract_returns_contract(monkeypatch):
    expected = Contract(preconditions=["a and b are integers"], postconditions=["returns a + b"])
    fake = _FakeCompletions(result=expected)
    monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = inference.infer_contract(_meta(), settings)

    assert result is expected
    assert fake.kwargs["model"] == settings.model_standard  # default tier
    assert fake.kwargs["api_key"] == "test-key"
    assert fake.kwargs["response_model"] is Contract


def test_infer_contract_uses_requested_tier(monkeypatch):
    fake = _FakeCompletions(result=Contract())
    monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    inference.infer_contract(_meta(), settings, model_tier="premium")

    assert fake.kwargs["model"] == settings.model_premium


def test_infer_contract_missing_key_raises_pipeline_error(monkeypatch):
    settings = _settings_no_key(monkeypatch)
    with pytest.raises(PipelineError) as exc_info:
        inference.infer_contract(_meta(), settings)
    assert exc_info.value.stage == "contract_inference"


def test_infer_contract_wraps_llm_failure(monkeypatch):
    fake = _FakeCompletions(exc=RuntimeError("boom"))
    monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    with pytest.raises(PipelineError) as exc_info:
        inference.infer_contract(_meta(), settings)
    assert exc_info.value.stage == "contract_inference"
    assert "boom" in exc_info.value.detail
