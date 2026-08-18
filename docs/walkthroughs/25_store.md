# 25 — `src/typewright/store.py` (saving runs for shareable links)

## 1. What this file is for

When someone analyzes a function on the web demo, the result has an `analysis_id`. This file
is what lets you come back to that result **later, by id** — the thing behind a shareable link
like `…/v1/runs/abc-123`. Without it, every result vanishes the moment you close the tab.

Think of it as a **coat check**. When an analysis finishes, we hand the whole result to the
coat check and get a ticket (the `analysis_id`). Later, anyone with the ticket can hand it back
and get the exact same result returned. The coat check itself is a tiny single-file database.

The key design idea: we don't bake one specific database into the rest of the app. Instead we
define a small **interface** — "a thing that can `save` a result and `load` one back" — and
provide two versions of it: a real one that writes to a file on disk, and a fake one that just
keeps things in memory (used by tests, so they never touch the disk).

## 2. A mental model

1. **Store the whole answer as one lump of text.** A finished analysis is a Pydantic object
   (`AnalyzeResponse`). Pydantic can turn it into a JSON string and back again perfectly. So we
   don't design a big table with a column per field — we just save the JSON string in one column,
   keyed by the id. To read it back: load the string, ask Pydantic to rebuild the object.

2. **An interface, two implementations.** `RunStore` is the *contract* (save / load). `SqliteRunStore`
   is the real, durable one (a file). `InMemoryRunStore` is a throwaway one (a dictionary). The
   rest of the app only knows about the contract, so we can swap the real backend later (e.g. to
   Postgres) without touching any route.

3. **A fresh database connection each time.** A web server handles many requests at once, on
   different threads. SQLite connections don't like being shared across threads, so each `save`
   and `load` opens its own connection, does its one job, and closes it. That keeps things simple
   and safe; the cost (opening a SQLite file) is tiny.

## 3. The whole file

```python
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
```

## 4. Step-by-step

**`RunStore` (the contract).** A `typing.Protocol` — Python's way of saying "anything with these
two methods counts." Neither concrete class has to *inherit* from it; they just need a matching
`save` and `load`. That's why a test can even pass a tiny hand-written `BoomStore` and the app
accepts it. The route code is typed `store: RunStore`, so it speaks only to the contract.

**`InMemoryRunStore` (the fake).** A dictionary from id → JSON string. `save` stores the JSON;
`load` rebuilds the object (or returns `None` if the id isn't there). It's what the API tests use,
so the whole suite runs without ever creating a file. (It's also fine for a quick local run where
you don't care if results survive a restart.)

**`SqliteRunStore.__init__`.** On creation it makes sure the `runs` table exists
(`CREATE TABLE IF NOT EXISTS …`). Three columns: the id (primary key, so re-saving the same id
replaces rather than duplicates), a timestamp, and the JSON body. The `with conn:` block commits
the change; the `finally: conn.close()` guarantees the connection is released even if something
goes wrong.

**`_connect`.** Opens a SQLite connection and switches it to **WAL** (write-ahead logging) mode,
which lets readers and a writer coexist without blocking each other — useful when several requests
hit the store at once.

**`save`.** Stamps the current UTC time, then `INSERT OR REPLACE` — so saving the same `analysis_id`
twice just overwrites, never errors. The body is `response.model_dump_json()` — Pydantic's exact
JSON form of the whole result.

**`load`.** Looks up the row by id. If there's no row, returns `None` (the route turns that into a
404). If there is, `AnalyzeResponse.model_validate_json(row[0])` rebuilds the original object, which
the route returns unchanged.

**Where it's wired:** `main.py` adds a `get_run_store` dependency (the same injectable-seam pattern
as the LLM and sandbox steps), `POST /v1/analyze` calls `store.save(response)` **best-effort** before
returning, and `GET /v1/runs/{analysis_id}` calls `store.load(...)`. See `06_main.md`.

## 5. What could go wrong (and why the code is shaped to avoid it)

- **A storage hiccup losing a real result.** The analysis is the valuable thing; the shareable link
  is a bonus. So `main.py` wraps `store.save` in a try/except and logs-and-continues on failure —
  the request still returns its 200 with the bugs. A full disk must degrade the *link*, not the
  *answer* (the same best-effort rule as the Phase 6 fix step, D44/D50).
- **Sharing a SQLite connection across threads.** A web server runs request handlers on a threadpool,
  and SQLite objects aren't thread-safe to share. Opening a fresh connection per call sidesteps the
  whole problem — no shared state, no "SQLite objects created in a thread can only be used in that
  same thread" error.
- **Designing a brittle table schema.** If we'd made a column per field, every change to
  `AnalyzeResponse` (a new field, a new phase) would need a migration. Storing the whole object as
  one JSON blob means the storage format *follows* the model for free; the read path is one row plus
  one `model_validate_json`.
- **Tests touching the disk.** Because the store is injected through `get_run_store`, the test suite
  overrides it with `InMemoryRunStore`, so no `runs.db` file appears during tests. The store's own
  tests (`test_store.py`) use a real `SqliteRunStore` but point it at pytest's temporary directory,
  which is cleaned up automatically.
- **Links dying on every redeploy.** SQLite writes to a file. In a container, that file lives inside
  the container unless you mount a volume — so for a real deployment, point `runs_db_path` at a
  mounted path. (This is also why we chose SQLite over reusing Redis, whose default is in-memory and
  would lose every link on restart.)
- **Locking the door to one database forever.** The `RunStore` protocol is the escape hatch: when a
  future deploy needs multiple replicas sharing one store, a `PostgresRunStore` can implement the
  same two methods and drop in with no change to any route (D50).

## 6. Change history

- **2026-06-28** — **Created (Phase 8, Unit 2a, D50).** The run store behind shareable links: a
  `RunStore` protocol with `SqliteRunStore` (durable single-file DB, stdlib `sqlite3`, connection per
  call, WAL; one row = id + UTC `created_at` + the `AnalyzeResponse` JSON body) and `InMemoryRunStore`
  (the dict fake for tests). Wired in `main.py`: `POST /v1/analyze` persists best-effort via a new
  `get_run_store` seam; `GET /v1/runs/{analysis_id}` returns the stored response or 404. New config
  `runs_db_path` (default `runs.db`). No eviction yet (`created_at` recorded for a future TTL pass).
  See `06_main.md` (the route + persist) and `07_tests.md` (`test_store.py` + the API tests).
