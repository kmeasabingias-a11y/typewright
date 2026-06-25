"""Tests for the GitHub App client (Phase 7). httpx mocked via MockTransport; no network."""

import json

import httpx
import pytest

from typewright import github
from typewright.config import Settings
from typewright.errors import GitHubError


def _mock_client(handler, token=None):
    """An httpx.Client wired to a MockTransport, mirroring github._client's auth header."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
        headers=headers,
    )


def _settings():
    return Settings(_env_file=None, github_app_id="123", github_app_private_key_path="/x.pem")


def test_installation_token_returns_token(monkeypatch):
    monkeypatch.setattr(github, "_app_jwt", lambda settings: "fake-jwt")

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/app/installations/42/access_tokens"
        assert request.headers["Authorization"] == "Bearer fake-jwt"
        return httpx.Response(201, json={"token": "ghs_abc", "expires_at": "2026-01-01T00:00:00Z"})

    monkeypatch.setattr(github, "_client", lambda token: _mock_client(handler, token))
    assert github.installation_token(42, _settings()) == "ghs_abc"


def test_installation_token_unconfigured_raises():
    with pytest.raises(GitHubError):
        github.installation_token(42, Settings(_env_file=None))


def test_installation_token_http_error_raises(monkeypatch):
    monkeypatch.setattr(github, "_app_jwt", lambda settings: "fake-jwt")
    monkeypatch.setattr(
        github, "_client",
        lambda token: _mock_client(lambda req: httpx.Response(401, json={"message": "bad"}), token),
    )
    with pytest.raises(GitHubError):
        github.installation_token(42, _settings())


def test_list_pr_files_paginates(monkeypatch):
    page1 = [{"filename": f"f{i}.py", "patch": "@@"} for i in range(100)]
    page2 = [{"filename": "last.py", "patch": "@@"}]

    def handler(request):
        page = int(dict(request.url.params)["page"])
        return httpx.Response(200, json=page1 if page == 1 else page2)

    monkeypatch.setattr(github, "_client", lambda token: _mock_client(handler, token))
    files = github.list_pr_files("octo/repo", 7, "tok")
    assert len(files) == 101
    assert files[-1]["filename"] == "last.py"


def test_list_pr_files_single_page(monkeypatch):
    monkeypatch.setattr(
        github, "_client",
        lambda token: _mock_client(lambda req: httpx.Response(200, json=[{"filename": "a.py"}]), token),
    )
    assert [f["filename"] for f in github.list_pr_files("octo/repo", 7, "tok")] == ["a.py"]


def test_list_pr_files_http_error_raises(monkeypatch):
    monkeypatch.setattr(
        github, "_client", lambda token: _mock_client(lambda req: httpx.Response(500), token)
    )
    with pytest.raises(GitHubError):
        github.list_pr_files("octo/repo", 7, "tok")


def test_post_comment_sends_body(monkeypatch):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1})

    monkeypatch.setattr(github, "_client", lambda token: _mock_client(handler, token))
    github.post_comment("octo/repo", 7, "Found 2 bugs", "tok")
    assert seen["path"] == "/repos/octo/repo/issues/7/comments"
    assert seen["payload"] == {"body": "Found 2 bugs"}


def test_post_comment_http_error_raises(monkeypatch):
    monkeypatch.setattr(
        github, "_client", lambda token: _mock_client(lambda req: httpx.Response(403), token)
    )
    with pytest.raises(GitHubError):
        github.post_comment("octo/repo", 7, "x", "tok")