# 18 — `src/typewright/webhook.py`

## What this file is for

This file is TypeWright's **doorman for GitHub**. When someone opens or updates a pull request on a
repo where TypeWright is installed, GitHub sends a message ("a webhook") to our server. This file
does the two careful checks at the door before any work is allowed in:

1. **Is this message really from GitHub?** (signature check)
2. **Is it a message we actually care about?** (a pull request being opened/updated — not a label
   change, not some other event)

It's deliberately just the *checking* — two small, pure functions. The actual "open the door, take
the package, hand it to the kitchen" part (reading the request, queuing the work, replying) lives in
the web route (`main.py`, unit 06). Keeping the checks separate makes the security-critical bit easy
to test on its own.

It exposes two functions:
- `verify_signature(body, signature_header, secret)` → `True`/`False`
- `parse_pull_request_event(event_type, payload)` → a `PullRequestJob`, or `None` to ignore

---

## A mental model: a signed letter, and a "is this my kind of mail?" sort

Think of each webhook as a **signed letter**. GitHub and TypeWright share a **secret password** (the
webhook secret you set when registering the App). GitHub uses that password to compute a fingerprint
of the *exact* letter it's sending, and writes that fingerprint on the envelope (`X-Hub-Signature-256`).
We re-compute the fingerprint from the letter we received using the same password — if they match, the
letter is genuinely from GitHub and wasn't tampered with. If they don't, we refuse it.

The crucial subtlety: we must fingerprint the **exact raw bytes** GitHub sent, not a "cleaned up"
version. If we parsed the JSON and re-serialized it, the spacing or key order might differ by a byte —
and the fingerprint wouldn't match. So the route hands us the *raw body*, untouched.

The second function is a simple sort: of all the GitHub events, we only act on a pull request being
**opened**, **synchronize**d (new commits pushed), or **reopened**. Everything else — a label added, a
comment, a different event type entirely — we wave through and ignore.

---

## The whole file

```python
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
```

---

## Step-by-step

### `verify_signature(...)`

- GitHub's header looks like `sha256=ab12cd…`. If it's missing, or doesn't start with `sha256=`, we
  bail immediately with `False` (no secret to check, nothing to trust).
- `hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()` is us re-computing the fingerprint of
  the raw body with the shared secret.
- `hmac.compare_digest(...)` compares our fingerprint with theirs **in constant time** — meaning it
  doesn't return faster for a "more wrong" guess. That stops a sneaky attacker from learning the
  secret one character at a time by measuring how long the comparison takes. (A plain `==` would leak
  that timing.)

### `parse_pull_request_event(...)`

- First gate: is the event type literally `"pull_request"`? If not (it's a `ping`, an `issues` event,
  whatever), return `None`.
- Second gate: is the `action` one we care about (`opened`/`synchronize`/`reopened`)? A PR getting a
  label or being closed isn't a reason to re-test it, so → `None`.
- Then it plucks out the four facts the worker will need — the repo's `owner/name`, the PR number, the
  head commit's SHA, and the installation id — and packs them into a `PullRequestJob`.
- The whole extraction is wrapped in `try/except (KeyError, TypeError)`: if the payload is shaped
  oddly and a field is missing, we return `None` (ignore it) rather than crashing the server.

---

## What could go wrong

### 1. Fingerprinting the wrong bytes
The single easiest way to break webhook security is to verify against re-serialized JSON instead of the
raw body. The route reads the raw bytes and passes them straight here; we never re-encode. (This is
why verification lives as a function taking `bytes`, not a parsed dict.)

### 2. A timing side-channel
Comparing the fingerprints with `==` would return as soon as two characters differ — and an attacker
could time that to slowly guess a valid signature. `hmac.compare_digest` always takes the same time.

### 3. Crashing on a weird payload
GitHub payloads are huge and occasionally a field you expect isn't there. Letting a `KeyError` bubble
up would turn a malformed event into a 500. We catch it and return `None` — the route then just
acknowledges and ignores, which is the safe, quiet behavior.

### 4. Acting on the firehose
A busy PR fires many events (labels, assignments, reviews). If we analyzed on *every* `pull_request`
action we'd waste a lot of LLM + sandbox work re-testing unchanged code. The `_ACTIONABLE` set keeps us
to the three actions where the code might actually have changed.

---

## Summary

`webhook.py` is the security gate for Phase 7: two pure functions that (a) confirm a webhook is genuinely
from GitHub by re-computing an HMAC-SHA256 fingerprint of the **raw** body against the shared secret
(constant-time compare), and (b) extract a minimal `PullRequestJob` only for the PR actions worth acting
on, returning `None` for everything else. Both are pure and unit-tested directly; the route in `main.py`
owns the I/O around them (read body → verify → parse → enqueue → reply). This is decision **D47**.

---

## Change history

- **2026-06-25** — Created in Phase 7, Unit 1 (D47). `verify_signature` (raw-body HMAC-SHA256, constant-time)
  + `parse_pull_request_event` (→ `PullRequestJob` for opened/synchronize/reopened, else `None`). Pure
  functions; the `POST /webhook/github` route (unit 06) composes them. Verified live in the Phase-7 smoke:
  a real PR's signed delivery passed verification and enqueued.
