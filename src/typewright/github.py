"""Phase 7: a thin GitHub App REST client — installation auth + the two calls the bot needs.

Mirrors ``kestrel.py`` (sync httpx + an ``_client()`` test seam); the worker offloads the whole
blocking job to a thread (Unit 5), so this stays simple synchronous code. Three operations:

* ``installation_token`` — mint a short-lived installation access token: build an App JWT
(RS256, signed with the App's private key) and exchange it at
``POST /app/installations/{id}/access_tokens``.
* ``list_pr_files`` — the PR's changed files (each with its unified-diff ``patch``), paginated.
* ``post_comment`` — post the findings as a single issue comment on the PR.

Any HTTP/transport failure raises ``GitHubError`` (handled by the worker; not request-scoped).
Tests monkeypatch ``_client`` (httpx.MockTransport) and ``_app_jwt`` so no real key or network is
needed; ``_app_jwt``'s real signing is covered by the live smoke (D48).
"""

from __future__ import annotations

import time

import httpx
import jwt

from .config import Settings, get_settings
from .errors import GitHubError

_API = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _app_jwt(settings: Settings) -> str:
    """Build a short-lived App JWT (RS256) from the App id + private-key file (D48)."""
    with open(settings.github_app_private_key_path, "r", encoding="utf-8") as fh:
        private_key = fh.read()
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 9 * 60, "iss": settings.github_app_id},
        private_key,
        algorithm="RS256",
    )


def _client(token: str) -> httpx.Client:
    """Build the httpx client for one GitHub call (test seam: monkeypatch this)."""
    return httpx.Client(
        base_url=_API,
        headers={**_HEADERS, "Authorization": f"Bearer {token}"},
        timeout=30.0,
    )


def installation_token(installation_id: int, settings: Settings | None = None) -> str:
    """Mint an installation access token for ``installation_id``."""
    settings = settings or get_settings()
    if not settings.github_app_id or not settings.github_app_private_key_path:
        raise GitHubError("GitHub App not configured (github_app_id + github_app_private_key_path)")
    try:
        with _client(_app_jwt(settings)) as client:
            resp = client.post(f"/app/installations/{installation_id}/access_tokens")
            resp.raise_for_status()
            return resp.json()["token"]
    except (httpx.HTTPError, KeyError) as exc:
        raise GitHubError(f"installation token request failed: {exc}") from exc


def list_pr_files(repo_full_name: str, pr_number: int, token: str) -> list[dict]:
    """Return the PR's changed files (GitHub file objects with a ``patch``), following pages."""
    files: list[dict] = []
    page = 1
    try:
        with _client(token) as client:
            while True:
                resp = client.get(
                    f"/repos/{repo_full_name}/pulls/{pr_number}/files",
                    params={"per_page": 100, "page": page},
                )
                resp.raise_for_status()
                batch = resp.json()
                files.extend(batch)
                if len(batch) < 100:
                    return files
                page += 1
    except httpx.HTTPError as exc:
        raise GitHubError(f"listing PR files failed: {exc}") from exc


def post_comment(repo_full_name: str, pr_number: int, body: str, token: str) -> None:
    """Post ``body`` as an issue comment on the PR (PR comments are issue comments)."""
    try:
        with _client(token) as client:
            resp = client.post(
                f"/repos/{repo_full_name}/issues/{pr_number}/comments",
                json={"body": body},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise GitHubError(f"posting comment failed: {exc}") from exc