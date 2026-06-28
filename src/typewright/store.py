"""Persistence for completed analyses, so a run can be fetched by id (Phase 8, Unit 2, D50).

The web demo (and the brief's ``GET /v1/runs/{analysis_id}``) needs a previously-run analysis to be
fetchable later — a shareable link. We store each ``AnalyzeResponse`` as a single JSON blob keyed by
its ``analysis_id``, behind a small ``RunStore`` interface so the storage choice stays swappable and
the API tests can inject an in-memory fake (the same dependency-seam idiom as the LLM/sandbox steps).

``SqliteRunStore`` is the real one: stdlib ``sqlite3``, a single-file database, no separate service —
the lightest thing that durably backs the feature (DECISIONS.md D1/D12/D50). It opens a short-lived
connection per call so it is safe to use from FastAPI's worker threadpool, and runs in WAL mode for
concurrent readers. ``InMemoryRunStore`` is the dict-backed fake used by tests and throwaway dev runs.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Protocol

from .models import AnalyzeResponse


class RunStore(Protocol):
    """A place to save a completed analysis and fetch it back by id."""

    def save(self, response: AnalyzeResponse) -> None: ...

    def load(self, analysis_id: str) -> AnalyzeResponse | None: ...


class InMemoryRunStore:
    """A dict-backed RunStore — no disk, no durability. For tests and throwaway dev runs."""

    def __init__(self) -> None:
        self._runs: dict[str, str] = {}

    def save(self, response: AnalyzeResponse) -> None:
        self._runs[response.analysis_id] = response.model_dump_json()

    def load(self, analysis_id: str) -> AnalyzeResponse | None:
        body = self._runs.get(analysis_id)
        return AnalyzeResponse.model_validate_json(body) if body is not None else None


class SqliteRunStore:
    """A durable RunStore backed by a single SQLite file (stdlib sqlite3; D50).

    Each analysis is one row: the ``analysis_id`` primary key, a UTC ISO ``created_at`` (kept for a
    future TTL/cleanup pass — there is no eviction yet), and the full ``AnalyzeResponse`` as a JSON
    ``body``. A new connection is opened per operation, so the store is safe to call from FastAPI's
    threadpool without sharing a connection across threads.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS runs ("
                    "analysis_id TEXT PRIMARY KEY, "
                    "created_at TEXT NOT NULL, "
                    "body TEXT NOT NULL)"
                )
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def save(self, response: AnalyzeResponse) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO runs (analysis_id, created_at, body) VALUES (?, ?, ?)",
                    (response.analysis_id, created_at, response.model_dump_json()),
                )
        finally:
            conn.close()

    def load(self, analysis_id: str) -> AnalyzeResponse | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT body FROM runs WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
        finally:
            conn.close()
        return AnalyzeResponse.model_validate_json(row[0]) if row is not None else None