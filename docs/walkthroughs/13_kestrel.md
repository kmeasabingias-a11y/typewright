# 13 — `src/typewright/kestrel.py`

## What this file is for

This file is TypeWright's **phone line to the sandbox**.

Up to now, every phase has just *thought* about test code — it parses a function, works out
what properties it should have, writes Hypothesis strategies, and assembles a pytest file. But
it has never actually **run** any of it. Running unknown, freshly-generated code on our own
server would be reckless: a generated test could loop forever, eat all the memory, or do
something nasty. So we don't run it ourselves. We hand it to **Kestrel** — a separate service
whose entire job is to run Python inside a locked-down, throwaway container and tell us what
happened.

`kestrel.py` is the small piece of TypeWright that knows how to *make that phone call*: package
up the code, dial Kestrel's `/execute` endpoint, and hand back whatever came out.

Think of it like dropping a parcel at a bomb-disposal unit. We don't open the suspicious parcel
ourselves — we pass it through a hatch to the specialists, and they pass back a report:
"detonated safely, here's what was inside." This file is the hatch.

---

## A mental model: a thin wrapper over one endpoint

Two ideas make this file easy to read.

**1. We only use *one* of Kestrel's features.** Kestrel can do a lot — long-lived sessions,
live-streaming output, returning plots and data frames. TypeWright needs exactly **none** of
that. We need the simplest thing it offers: "here's a self-contained Python file, run it once,
tell me the result." That single feature is the stateless `POST /execute` endpoint. So this file
is deliberately tiny — a ~40-line wrapper around that one call, ignoring everything else Kestrel
can do.

Kestrel actually *ships* a full Python SDK (a ready-made client library). We chose **not** to
depend on it (decision **D37**): it isn't published anywhere we can easily install it from, and
99% of it is features we'll never touch. Re-creating the one slice we need keeps TypeWright
self-contained — it builds and runs without needing the Kestrel project sitting next to it.

**2. "A timeout is data, not a disaster."** This is the subtle rule that shapes the whole file.
When you call Kestrel, two very different kinds of things can go wrong:

- The **phone call itself** fails — Kestrel is down, the network drops, we sent a bad request.
  *That* is a real error: we raise and the request becomes a 500.
- The **code we sent** misbehaves — it crashes, or it runs too long and Kestrel kills it. That
  is **not** a phone-call failure. The call succeeded; Kestrel did its job and is calmly
  reporting "the thing you gave me failed/timed out." So a timeout comes back as an ordinary
  result with a `timed_out` flag set to `True` — *data we read*, not an exception we catch.

Mixing those two up is the classic mistake, so the file keeps them strictly apart.

---

## The whole file

```python
"""Thin client for the Kestrel sandbox's stateless /execute endpoint (Phase 5, D37).

TypeWright only needs Kestrel's stateless ``POST /execute``: submit one self-contained
Python file, get back captured stdout/stderr plus exit metadata. We deliberately do NOT
depend on the shipped ``kestrel_client`` SDK — it isn't published to a registry (a path
dep would be machine-specific, a git-subdir dep couples our build to the Kestrel repo),
and almost all of it (sessions, streaming, rich outputs) is surface we never touch. A
~40-line httpx wrapper over the one endpoint keeps TypeWright self-contained and
buildable in Docker/CI with no Kestrel checkout present.

Contract (mirrors Kestrel's own "timeout is data, not an error"): only a transport/HTTP
failure raises — as ``PipelineError(stage="sandbox_execution")`` -> HTTP 500. A run that
times out comes back as data (``SandboxResult.timed_out is True``); the caller maps that
to 504. ``run_in_sandbox`` is the seam the execution layer calls; tests monkeypatch the
``_client`` factory with an ``httpx.MockTransport`` so they never need a live Kestrel.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import Settings, get_settings
from .errors import PipelineError

_STAGE = "sandbox_execution"


@dataclass(frozen=True)
class SandboxResult:
    """The raw outcome of one /execute run (our mirror of Kestrel's ExecuteResult).

    Execution outcomes are DATA, not exceptions: a non-zero ``exit_code`` (pytest
    conventions: 0 pass, 1 failures, 2 collection error, 5 none collected) and a
    ``timed_out`` flag are normal results the caller inspects. The ``*_truncated``
    flags mean output hit Kestrel's byte cap.
    """

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "SandboxResult":
        return cls(
            stdout=d.get("stdout", ""),
            stderr=d.get("stderr", ""),
            exit_code=d.get("exit_code", 0),
            duration_ms=d.get("duration_ms", 0),
            timed_out=d.get("timed_out", False),
            stdout_truncated=d.get("stdout_truncated", False),
            stderr_truncated=d.get("stderr_truncated", False),
        )


def _client(settings: Settings, timeout: float) -> httpx.Client:
    """Build the httpx client for one /execute call.

    Factored out as the test seam (mirrors the LLM modules' ``_client()``): the suite
    monkeypatches this to return a client wired to an ``httpx.MockTransport``, so it
    exercises request building and response parsing with no live Kestrel.
    """
    headers: dict[str, str] = {}
    if settings.kestrel_api_key:
        headers["Authorization"] = f"Bearer {settings.kestrel_api_key}"
    return httpx.Client(
        base_url=settings.kestrel_base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
    )


def run_in_sandbox(
    code: str,
    *,
    timeout_seconds: float,
    settings: Settings | None = None,
) -> SandboxResult:
    """Run ``code`` in the Kestrel sandbox and return the raw result.

    ``timeout_seconds`` is the per-run sandbox budget (Kestrel clamps it down to its
    own ceiling). The httpx read timeout is set above that budget so the HTTP call
    outlives a legitimately long run rather than aborting it client-side. Any
    transport/HTTP error becomes a ``PipelineError`` (stage "sandbox_execution");
    a timed-out run is returned as data (``timed_out=True``), not raised.
    """
    settings = settings or get_settings()
    http_timeout = timeout_seconds + settings.kestrel_http_timeout_buffer_seconds
    try:
        with _client(settings, http_timeout) as client:
            response = client.post(
                "/execute",
                json={"code": code, "timeout_seconds": timeout_seconds},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise PipelineError(_STAGE, f"Kestrel /execute failed: {exc}") from exc
    return SandboxResult.from_dict(payload)
```

---

## Step-by-step

### `SandboxResult` — the report card

```python
@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool
    stdout_truncated: bool = False
    stderr_truncated: bool = False
```

This is our copy of the shape Kestrel sends back. It's a **frozen dataclass** — a small,
read-only bundle of values (`frozen=True` means once it's made, nobody can change its fields). We
use a plain dataclass here rather than the Pydantic models the rest of the project uses, because
this is just a transport receipt — a faithful mirror of what came down the wire — not one of our
own domain shapes.

The fields, in plain terms:

- `stdout` / `stderr` — the text the program printed (normal output / error output). This is
  where pytest's report and Hypothesis's "Falsifying example" lines will appear — the next unit
  reads them to find bugs.
- `exit_code` — the program's exit number. For pytest this is a code with meaning: **0** = all
  tests passed, **1** = at least one failed (we found a bug!), **2** = the file couldn't even be
  collected, **5** = no tests were found.
- `duration_ms` — how long it ran, in milliseconds.
- `timed_out` — `True` if Kestrel had to kill it for running too long.
- `stdout_truncated` / `stderr_truncated` — `True` if the output was so big Kestrel chopped it
  off at its size cap. A flag worth checking before trusting that we saw the *whole* report.

`from_dict` builds one of these from the JSON Kestrel returns, using `.get(...)` with safe
defaults so a missing field never crashes us.

### `_client(...)` — the test seam

```python
def _client(settings: Settings, timeout: float) -> httpx.Client:
    headers: dict[str, str] = {}
    if settings.kestrel_api_key:
        headers["Authorization"] = f"Bearer {settings.kestrel_api_key}"
    return httpx.Client(base_url=..., headers=headers, timeout=timeout)
```

`httpx` is the library that makes HTTP calls (the same one hiding under the test client we already
use). This little function builds a configured `httpx.Client`:

- If we have a Kestrel API key, it adds an `Authorization: Bearer <key>` header — the standard
  "here's my key" format Kestrel expects. **If there's no key, no header is added** — because
  Kestrel can run with authentication switched off (which is the normal setup when you run it on
  your own machine for local development).
- It sets the `base_url` (where Kestrel lives) and a `timeout`.

Why is this its own function? The same reason the AI modules each keep a `_client()` (see unit
10): it's the **seam the tests grab**. A test swaps out `kestrel._client` for a version wired to
a fake transport, so the whole suite can exercise this file *without a real Kestrel running
anywhere*. The matching-power-socket idea from unit 10 applies here too.

### `run_in_sandbox(...)` — the actual call

```python
def run_in_sandbox(code, *, timeout_seconds, settings=None) -> SandboxResult:
    settings = settings or get_settings()
    http_timeout = timeout_seconds + settings.kestrel_http_timeout_buffer_seconds
    try:
        with _client(settings, http_timeout) as client:
            response = client.post("/execute", json={"code": code, "timeout_seconds": timeout_seconds})
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise PipelineError(_STAGE, f"Kestrel /execute failed: {exc}") from exc
    return SandboxResult.from_dict(payload)
```

This is the whole job. Reading it top to bottom:

- **`timeout_seconds` is the sandbox budget** — how long the code is allowed to run *inside*
  Kestrel. Kestrel will clamp it down to its own maximum if we ask for more (we can ask for less,
  never more).
- **`http_timeout = timeout_seconds + buffer`** — this is the subtle bit. There are *two*
  timeouts in play: how long the code may run inside the sandbox, and how long our HTTP call is
  willing to wait for an answer. If the second were shorter than the first, we'd hang up the phone
  *while Kestrel was still legitimately running our code* — and never hear the result. So we give
  the HTTP call the sandbox budget **plus a margin** (the buffer from config), guaranteeing the
  phone call outlives the work it's waiting on.
- **`client.post("/execute", json={...})`** — the call itself: send the code and the budget as
  JSON.
- **`response.raise_for_status()`** — if Kestrel answered with an error status (401 unauthorized,
  429 too many requests, 500, …), this turns it into an exception.
- **`except httpx.HTTPError`** — this catches *both* a bad status (from the line above) and a
  genuine connection failure (Kestrel unreachable, timed-out HTTP call). Either way it's a
  *phone-call* failure, so we re-raise it as a `PipelineError` tagged with the stage
  `"sandbox_execution"` — which the API layer (unit 06) already knows how to turn into a 500 that
  names the failing stage.
- **The happy path** returns `SandboxResult.from_dict(payload)` — the report card.

Notice what is *not* in the `except`: a timeout of the **code** doesn't come through here at all.
That's a normal 200 response from Kestrel with `timed_out: true` in the body, so it flows
straight through to `from_dict` and comes back as `SandboxResult(timed_out=True)`. Data, not an
error — exactly as the mental model promised.

---

## What could go wrong

### 1. Treating a code-timeout as a crash
The easiest mistake: assume "timeout" means the request failed and raise an error. It doesn't.
Kestrel running our code and reporting "that took too long, I stopped it" is a *successful* call
with a useful result. We deliberately let it through as data (`timed_out=True`); a later unit maps
that to the right "your tests exceeded the time budget" response (a 504), separate from "the
sandbox itself broke" (a 500).

### 2. The HTTP timeout being shorter than the sandbox budget
If we waited, say, 30 seconds for the HTTP reply but allowed the code 30 seconds to run, we'd
routinely hang up right as Kestrel was about to answer — turning healthy long runs into phantom
failures. The `+ buffer` margin exists precisely to stop that. It's a small line with a big
consequence.

### 3. Depending on the full Kestrel SDK
Pulling in Kestrel's published client library would have coupled our build to a package that
isn't easily installable and dragged in piles of features we don't use (sessions, streaming).
Writing the one slice we need keeps TypeWright able to build on its own (D37). The trade-off is a
few lines of request/response code we now own — a fair price for not being tied to a neighbour.

### 4. Forgetting that output can be truncated
Kestrel caps how much text it sends back. If a test produces a huge report, `stdout_truncated`
comes back `True` and we may be missing the tail — including, possibly, a failing example. The
flag is on the result for exactly this reason; the result-parsing unit must respect it rather
than assuming it always saw everything.

---

## Summary

`kestrel.py` is TypeWright's thin client for the Kestrel sandbox — a small wrapper over the one
endpoint we use, `POST /execute`. `run_in_sandbox` packages the code and a time budget, makes the
call (giving the HTTP request a longer timeout than the sandbox budget so it never hangs up
early), and returns a `SandboxResult` — a frozen mirror of Kestrel's reply (`stdout`, `stderr`,
`exit_code`, `timed_out`, …). The guiding rule is "a timeout is data, not a disaster": only a real
phone-call failure raises (as a `PipelineError` → 500); a run that crashes or times out comes back
as an ordinary result for a later unit to interpret. We deliberately built this slice ourselves
rather than depending on Kestrel's full SDK (D37), keeping TypeWright self-contained. The
`_client()` function is the seam tests swap out so the suite never needs a live Kestrel.

---

## Change history

- **2026-06-19** — Created in Phase 5, Unit 1. Holds `SandboxResult` (frozen dataclass mirroring
  Kestrel's `ExecuteResponse`), `_client()` (the httpx-client seam, adds `Authorization: Bearer`
  only when a key is set), and `run_in_sandbox()` (POST `/execute`; HTTP timeout = run budget +
  `kestrel_http_timeout_buffer_seconds`; only transport/HTTP failures raise as
  `PipelineError(stage="sandbox_execution")`; a code timeout returns as data). We build this thin
  client rather than depend on the shipped `kestrel_client` SDK (D37). The contract was verified
  against the real Kestrel server source (request/response schemas, the `Bearer` auth scheme, and
  the route returning a 200-with-`timed_out` while clamping the timeout down to its ceiling). Suite
  green at 57 passed.
- **2026-06-28** — Phase 9 (Unit 5, D55): split the error path. A transport error (Kestrel unreachable) or a
  **transient** status from Kestrel (429/502/503/504) now raises `SandboxUnavailableError(retry_after)` (→
  **503** + `Retry-After`) instead of `PipelineError` — "the sandbox is busy/down, retry" rather than "we
  broke." Any other non-2xx still becomes `PipelineError` → 500 (so the 500 test is unchanged). Reads the
  `Retry-After` header when present.
