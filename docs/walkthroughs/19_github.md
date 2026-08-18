# 19 — `src/typewright/github.py`

## What this file is for

This file is TypeWright's **phone line to GitHub**. The worker needs to do four things on GitHub's
side: prove who it is, find out which files a pull request changed, read those files, and post a
comment back. This file is the small, focused client that makes exactly those four API calls — and
nothing else.

It deliberately does **not** use a big GitHub library (like PyGithub). We only need four endpoints out
of hundreds, so a thin hand-written client (mirroring how `kestrel.py` talks to Kestrel, unit 13)
keeps things small and easy to test.

It exposes:
- `installation_token(installation_id)` → a short-lived access token
- `list_pr_files(repo, pr_number, token)` → the PR's changed files (with their diffs)
- `get_file_content(repo, path, ref, token)` → the full text of one file at a commit
- `post_comment(repo, pr_number, body, token)` → posts the findings comment

---

## A mental model: a day pass earned with a signed badge

A GitHub App doesn't log in with a password. It proves its identity with a **signed badge** (a JWT —
a small token signed with the App's *private key*, which only we have). You can't use that badge to do
much directly; instead you show it at the front desk and get a **day pass** scoped to one installation
(`installation_token`). That day pass is what actually opens doors — reading files, posting comments —
and it expires within an hour, so even if it leaked it's only briefly useful.

So every job follows the same shape: *make the badge → trade it for a day pass → use the pass for the
real calls.*

Two more design notes:
- **It's plain synchronous code.** The worker runs this whole thing on a background thread (unit 23),
  so there's no need for async here — and it keeps the code (and its tests) identical in shape to
  `kestrel.py`.
- **Any failure raises `GitHubError`.** That's a special error type (unit 04) that is *not* tied to a
  web request — the worker catches it, logs it, and moves on. It never becomes an HTTP status code,
  because by the time we're talking to GitHub, there's no caller waiting on a response.

---

## The whole file

```python
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


def get_file_content(repo_full_name: str, path: str, ref: str, token: str) -> str:
    """Fetch the raw text of a file at ``ref`` (a commit SHA), via the contents API."""
    try:
        with _client(token) as client:
            resp = client.get(
                f"/repos/{repo_full_name}/contents/{path}",
                params={"ref": ref},
                headers={"Accept": "application/vnd.github.raw+json"},
            )
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as exc:
        raise GitHubError(f"fetching {path}@{ref[:7]} failed: {exc}") from exc


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
```

---

## Step-by-step

### `_app_jwt(...)` — the signed badge
Reads the App's private key from the `.pem` file, then builds a JWT with three claims: `iat` (issued-at,
set 60 seconds in the past to tolerate small clock differences), `exp` (expires in 9 minutes — GitHub
caps App JWTs at 10), and `iss` (the App id). It's signed with **RS256** (asymmetric: we sign with the
private key, GitHub verifies with the matching public key it stored when you registered the App).

### `_client(token)` — the test seam
Builds an `httpx.Client` pointed at `api.github.com` with the right `Accept`/version headers and an
`Authorization: Bearer <token>`. Factored out so tests can swap it for a `MockTransport` (canned
responses, no network), exactly like `kestrel.py`.

### `installation_token(...)` — trade the badge for a day pass
First a guard: if the App id or key path isn't configured, raise `GitHubError` early (clearer than a
confusing file error later). Then it builds the JWT, `POST`s to `…/access_tokens`, and returns the
`token` from the reply. Any HTTP error or a missing `token` field becomes a `GitHubError`.

### `list_pr_files(...)` — what changed
Calls `…/pulls/{n}/files`, 100 at a time, following pages until a page comes back with fewer than 100
items (the last page). Each item is GitHub's file object, importantly carrying a `patch` (the unified
diff for that file) and a `status` (`modified`/`added`/`removed`). The worker uses both.

### `get_file_content(...)` — read a file at a commit
The `patch` only shows the *changed lines*, not the whole file — but to parse functions we need the
whole file. This fetches it via the contents API with the **raw** media type (`Accept:
application/vnd.github.raw+json`), so the response body *is* the file text (not base64-wrapped JSON).
We ask at a specific `ref` (the PR's head commit) so we read exactly the version under review.

### `post_comment(...)` — the payoff
Posts the markdown body to `…/issues/{n}/comments`. (GitHub treats a PR as an issue for commenting, so
this is the issue-comments endpoint.) Success is a 201; any error → `GitHubError`.

---

## What could go wrong

### 1. Taking a heavy dependency for a thin need
PyGithub would pull in a large surface to use four endpoints. A ~60-line client keeps the build small
and the tests trivial (MockTransport), the same reasoning as the Kestrel client (D37).

### 2. A leaked long-lived credential
We never ship the private key anywhere or hold a long-lived token. The App JWT lives ~9 minutes; the
installation token GitHub returns expires within an hour. Each job mints a fresh one.

### 3. Reading the wrong version of a file
If we fetched a file at the default branch instead of the PR's head commit, we'd analyze code that
isn't what's being proposed. `get_file_content` always passes `ref=<head sha>`.

### 4. Silently missing files on big PRs
A PR with more than 100 changed files would only show the first page if we didn't paginate. The loop
follows pages until a short page ends it.

### 5. A GitHub hiccup taking down the worker
Network blips and rate limits happen. Every call wraps its errors as `GitHubError`, which the worker
catches per step — one failed call skips that piece, it doesn't crash the whole run.

---

## Summary

`github.py` is the Phase-7 GitHub client: a thin synchronous httpx wrapper over four endpoints —
mint an installation token (via an RS256-signed App JWT), list a PR's changed files (paginated), read a
file's raw content at the head commit, and post a comment. It mirrors `kestrel.py`'s shape (sync +
`_client()` seam → MockTransport tests), takes no heavy SDK, and turns every failure into a
non-request-scoped `GitHubError` the worker handles. This is decision **D48**.

---

## Change history

- **2026-06-25** — Created in Phase 7. Unit 2: `installation_token` (App JWT RS256 via PyJWT + private-key
  file → installation token), `list_pr_files` (paginated), `post_comment`; `_client()` test seam;
  `GitHubError` on any failure (D48). New dep `pyjwt[crypto]`. Unit 3 added `get_file_content` (raw media
  type, at a ref) for the diff→functions step. Verified live in the Phase-7 smoke (token minted, PR file
  listed + read, comment posted on a real PR).
