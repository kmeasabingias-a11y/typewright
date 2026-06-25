"""Phase 7: GitHub webhook signature verification + pull_request event parsing (no I/O).

Two pure functions the ``POST /webhook/github`` route composes:

* ``verify_signature`` — constant-time HMAC-SHA256 check of GitHub's ``X-Hub-Signature-256``
header against the RAW request body. GitHub signs the exact bytes it sent, so the route must
hash the raw body, never a re-serialized payload.
* ``parse_pull_request_event`` — pull the minimal ``PullRequestJob`` out of a ``pull_request``
event, but only for the actions worth analyzing (opened / synchronize / reopened); anything
else (a label change, a different event type, a malformed body) returns ``None`` so the route
acknowledges without enqueuing.

Both are deliberately pure (bytes/dict in, bool/model out) and unit-tested directly — the I/O
(reading the body, enqueuing, replying) stays in the route (D47).
"""

from __future__ import annotations

import hashlib
import hmac

from .models import PullRequestJob

_ACTIONABLE = {"opened", "synchronize", "reopened"}


def verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Return True iff ``signature_header`` is GitHub's valid HMAC-SHA256 of ``body``.

    GitHub sends ``X-Hub-Signature-256: sha256=<hex>``; compared with ``hmac.compare_digest``
    (constant time). A missing or malformed header is False.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256=") :])


def parse_pull_request_event(event_type: str, payload: dict) -> PullRequestJob | None:
    """Extract a ``PullRequestJob`` from a ``pull_request`` event, or ``None`` to ignore.

    Returns ``None`` for non-``pull_request`` events, for actions we don't act on (only
    opened / synchronize / reopened trigger analysis), and for a payload missing required
    fields — a malformed event is ignored, not a server error.
    """
    if event_type != "pull_request":
        return None
    if payload.get("action") not in _ACTIONABLE:
        return None
    try:
        pr = payload["pull_request"]
        return PullRequestJob(
            repo_full_name=payload["repository"]["full_name"],
            pr_number=pr["number"],
            head_sha=pr["head"]["sha"],
            installation_id=payload["installation"]["id"],
        )
    except (KeyError, TypeError):
        return None