"""Tests for the thin Kestrel /execute client (Phase 5, Unit 1).

No live Kestrel: the suite monkeypatches the ``_client`` factory to return an httpx
client backed by an ``httpx.MockTransport`` (D37). A timed-out run is asserted to be
*data* (not an exception); an HTTP error is asserted to raise ``PipelineError``.
"""

import json

import httpx
import pytest

from typewright import kestrel
from typewright.config import Settings
from typewright.errors import PipelineError, SandboxUnavailableError
from typewright.kestrel import SandboxResult

_OK_PAYLOAD = {
    "stdout": "1 passed",
    "stderr": "",
    "exit_code": 0,
    "duration_ms": 12,
    "timed_out": False,
    "stdout_truncated": False,
    "stderr_truncated": False,
}


def _mock_client(handler):
    return httpx.Client(
        base_url="http://kestrel.test", transport=httpx.MockTransport(handler)
    )


def _settings(**kw):
    return Settings(kestrel_base_url="http://kestrel.test", **kw)


def test_run_in_sandbox_posts_code_and_timeout(monkeypatch):
    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OK_PAYLOAD)

    monkeypatch.setattr(kestrel, "_client", lambda settings, timeout: _mock_client(handler))
    result = kestrel.run_in_sandbox("print('hi')", timeout_seconds=20.0, settings=_settings())

    assert captured["method"] == "POST"
    assert captured["path"] == "/execute"
    assert captured["body"] == {"code": "print('hi')", "timeout_seconds": 20.0}
    assert isinstance(result, SandboxResult)
    assert result.exit_code == 0
    assert result.stdout == "1 passed"


def test_api_key_sets_authorization_header():
    with kestrel._client(_settings(kestrel_api_key="secret"), 30.0) as client:
        assert client.headers["authorization"] == "Bearer secret"


def test_no_api_key_means_no_authorization_header():
    with kestrel._client(_settings(kestrel_api_key=None), 30.0) as client:
        assert "authorization" not in client.headers


def test_timed_out_run_is_data_not_error(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
                "duration_ms": 30000,
                "timed_out": True,
            },
        )

    monkeypatch.setattr(kestrel, "_client", lambda settings, timeout: _mock_client(handler))
    result = kestrel.run_in_sandbox("...", timeout_seconds=1.0, settings=_settings())

    assert result.timed_out is True
    assert result.exit_code == -1


def test_http_error_becomes_pipeline_error(monkeypatch):
    def handler(request):
        return httpx.Response(500, json={"detail": "boom"})

    monkeypatch.setattr(kestrel, "_client", lambda settings, timeout: _mock_client(handler))
    with pytest.raises(PipelineError) as excinfo:
        kestrel.run_in_sandbox("...", timeout_seconds=1.0, settings=_settings())

    assert excinfo.value.stage == "sandbox_execution"


def test_http_timeout_is_run_budget_plus_buffer(monkeypatch):
    captured = {}

    def fake_client(settings, timeout):
        captured["timeout"] = timeout
        return _mock_client(lambda request: httpx.Response(200, json=_OK_PAYLOAD))

    monkeypatch.setattr(kestrel, "_client", fake_client)
    kestrel.run_in_sandbox(
        "x", timeout_seconds=30.0, settings=_settings(kestrel_http_timeout_buffer_seconds=15.0)
    )

    assert captured["timeout"] == 45.0


def test_transient_status_is_sandbox_unavailable(monkeypatch):
    def handler(request):
        return httpx.Response(503, headers={"Retry-After": "7"}, json={"detail": "busy"})

    monkeypatch.setattr(kestrel, "_client", lambda settings, timeout: _mock_client(handler))
    with pytest.raises(SandboxUnavailableError) as excinfo:
        kestrel.run_in_sandbox("...", timeout_seconds=1.0, settings=_settings())
    assert excinfo.value.retry_after == 7


def test_transport_error_is_sandbox_unavailable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(kestrel, "_client", lambda settings, timeout: _mock_client(handler))
    with pytest.raises(SandboxUnavailableError):
        kestrel.run_in_sandbox("...", timeout_seconds=1.0, settings=_settings())