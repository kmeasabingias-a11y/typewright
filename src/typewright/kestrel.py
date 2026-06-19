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