# 23 — `src/typewright/worker.py`

## What this file is for

This file is the **background worker** — the part that actually does the slow work of analyzing a pull
request, away from the web server. When the webhook accepts a PR (unit 06), it drops a small job onto a
queue and replies instantly. This file is what later picks that job up and runs the whole show: get a
GitHub token, find the changed Python functions, analyze each one, write a comment, and post it.

It has two halves:
- **`process_pr`** — the orchestration, written as plain testable code (no queue machinery). It ties
  together the GitHub client (unit 19), the diff parser (unit 20), the per-function analysis (unit 22),
  and the comment formatter (unit 21).
- **The arq plumbing** — `analyze_pr` (the queue task), `WorkerSettings` (the worker's config), and
  `enqueue` (how the web side pushes a job). This is the bit that needs Redis.

---

## A mental model: the kitchen behind the counter

The webhook is the counter: it takes your order and hands you a ticket immediately ("202, queued"). This
file is the **kitchen**: a separate worker process watches the queue (Redis), and when a ticket appears it
cooks the whole meal — which takes a couple of minutes of LLM calls and sandbox runs. Separating them
matters because GitHub won't wait minutes for a reply, and because the heavy work shouldn't tie up the web
server (or vanish if it restarts).

One important detail: the analysis pipeline is all **synchronous, blocking** code (LLM calls, sandbox
runs). But the queue worker (arq) is **async**. If we ran the blocking work directly in the async task it
would freeze the worker's event loop. So the task hands the blocking `process_pr` off to a background
thread (`asyncio.to_thread`) — the loop stays responsive, the work runs on the thread.

The worker is also **forgiving**: if one file can't be fetched, or one function can't be analyzed, it logs
it and moves on. One bad function shouldn't blank the whole PR. And it only comments when it actually finds
bugs — no noise on clean PRs.

---

## The whole file

```python
"""Phase 7: the background worker that analyzes a PR and comments (arq + Redis).

``process_pr`` is the orchestration (mockable, no arq): mint a token, list the PR's changed .py
files, fetch each file's new content, extract the changed top-level functions, run each through
``analyze_one``, render one comment, and post it. A per-function failure is logged and skipped —
one bad function doesn't sink the PR; the bot comments only when there are bugs.

``analyze_pr`` is the arq task wrapper; ``WorkerSettings`` is the arq worker config (run it with
``arq typewright.worker.WorkerSettings``); ``enqueue`` pushes a job from the web process. The
blocking pipeline (LLM + sandbox, all sync) is offloaded to a thread so it never blocks arq's
event loop.
"""

from __future__ import annotations

import asyncio
import logging

from arq import create_pool
from arq.connections import RedisSettings

from .analysis import analyze_one
from .comment import format_comment
from .config import Settings, get_settings
from .diff import changed_functions, changed_line_numbers
from .github import get_file_content, installation_token, list_pr_files, post_comment
from .models import FunctionFinding, PullRequestJob

logger = logging.getLogger("typewright")


def process_pr(job: PullRequestJob, settings: Settings | None = None) -> None:
    """Analyze a PR's changed functions and post one comment (the worker's whole job)."""
    settings = settings or get_settings()
    token = installation_token(job.installation_id, settings)
    files = list_pr_files(job.repo_full_name, job.pr_number, token)

    findings: list[FunctionFinding] = []
    for f in files:
        if f.get("status") == "removed" or not f.get("filename", "").endswith(".py"):
            continue
        try:
            content = get_file_content(job.repo_full_name, f["filename"], job.head_sha, token)
        except Exception as exc:  # noqa: BLE001 — skip a file we can't fetch, keep going
            logger.warning("skip file %s: %s", f.get("filename"), exc)
            continue
        for meta in changed_functions(content, changed_line_numbers(f.get("patch"))):
            try:
                findings.append(analyze_one(meta, settings))
            except Exception as exc:  # noqa: BLE001 — skip a function the pipeline can't analyze
                logger.warning("skip function %s: %s", meta.name, exc)

    if not any(fd.bugs for fd in findings):
        logger.info("no issues on %s #%d — not commenting", job.repo_full_name, job.pr_number)
        return
    post_comment(job.repo_full_name, job.pr_number, format_comment(findings), token)
    logger.info(
        "commented on %s #%d (%d function(s))",
        job.repo_full_name,
        job.pr_number,
        len(findings),
    )


async def analyze_pr(ctx, job_dict: dict) -> None:
    """arq task: run the (blocking) PR analysis in a thread so the event loop stays free."""
    await asyncio.to_thread(process_pr, PullRequestJob(**job_dict))


def _redis_settings(settings: Settings) -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def enqueue(job: PullRequestJob, settings: Settings | None = None) -> None:
    """Push a PR job onto the arq queue (called from the web process)."""
    settings = settings or get_settings()
    pool = await create_pool(_redis_settings(settings))
    try:
        await pool.enqueue_job("analyze_pr", job.model_dump())
    finally:
        await pool.aclose()


class WorkerSettings:
    """arq worker entrypoint: ``arq typewright.worker.WorkerSettings``."""

    functions = [analyze_pr]
    redis_settings = _redis_settings(get_settings())
```

---

## Step-by-step

### `process_pr(job)` — the orchestration
1. **Token.** Mint an installation access token for this PR's installation (unit 19).
2. **Files.** List the PR's changed files.
3. **For each file**: skip it unless it's a `.py` file that wasn't removed. Fetch its full content at the
   PR's head commit (wrapped in `try/except` — a fetch failure skips just that file).
4. **For each changed function** in that file (from `changed_line_numbers` + `changed_functions`, unit 20):
   run `analyze_one` (unit 22) and collect the `FunctionFinding`. A per-function failure is caught and
   skipped.
5. **Comment policy.** If no finding has any bugs, log and return — no comment (avoid noise on clean PRs).
   Otherwise, render one comment (unit 21) and post it (unit 19).

### `analyze_pr(ctx, job_dict)` — the queue task
arq calls this with a context and the job's data. It rebuilds the `PullRequestJob` and runs the blocking
`process_pr` on a **thread** (`asyncio.to_thread`) so arq's event loop isn't frozen during the
minutes-long analysis.

### `enqueue(job)` — pushing work from the web side
The webhook route calls this. It opens a short-lived Redis connection pool, pushes an `analyze_pr` job
carrying the job's fields, and closes the pool. (A shared, long-lived pool is a possible later
optimization; per-call is fine at PR volume.)

### `WorkerSettings` — the worker's config
arq's entrypoint, run from the command line as `arq typewright.worker.WorkerSettings`. It registers the
one task (`analyze_pr`) and points at Redis. This is a *separate process* from the web server.

---

## What could go wrong

### 1. Doing the work in the web request
GitHub needs a fast reply, and minutes-long work can't ride a web request (it would time out, and be lost
on a restart). The queue + separate worker is exactly what keeps the webhook instant and the work durable.

### 2. Freezing the worker on blocking calls
The pipeline is synchronous and slow; running it directly in the async task would block the event loop and
stall other jobs. `asyncio.to_thread` moves it off the loop.

### 3. One bad function (or file) killing the PR
A file that won't fetch, or a function the pipeline can't handle, is caught and skipped with a log — the
rest of the PR still gets analyzed. (This is the same best-effort philosophy as the fix step, D44/D46.)

### 4. Comment spam
Commenting on every PR — including clean ones — would be noise. The worker only comments when there's at
least one bug. (Re-commenting on every push is a known rough edge; updating a single comment in place is a
later hardening item.)

### 5. Analyzing non-Python or deleted files
The loop filters to `.py` files that weren't removed before doing any work, so we don't waste calls on
READMEs or deleted files.

---

## Summary

`worker.py` is the Phase-7 background worker: `process_pr` orchestrates the whole job (token → changed
`.py` files → changed functions → `analyze_one` each → one comment, posted only when bugs exist,
best-effort per file/function), and the arq layer (`analyze_pr`, `WorkerSettings`, `enqueue`) runs it off a
Redis queue in a separate process, offloading the blocking pipeline to a thread. This is the heart of
decision **D46** (arq/Redis, best-effort, comment-on-bugs).

---

## Change history

- **2026-06-25** — Created in Phase 7, Unit 5 (D46). `process_pr` orchestration (mockable, no arq):
  token → `list_pr_files` → filter `.py`/non-removed → `get_file_content` → `changed_functions` →
  `analyze_one` each → `format_comment` → `post_comment`; best-effort per file/function; comments only
  when bugs found. arq layer: `analyze_pr` (offloads blocking work via `asyncio.to_thread`),
  `WorkerSettings` (entrypoint `arq typewright.worker.WorkerSettings`), `enqueue` (pushes from the web
  process); `main.get_enqueue` now returns the real arq enqueue. New dep `arq`; config `redis_url`; first
  `docker-compose.yml` (redis). Ran the full chain live in the Phase-7 smoke (`analyze_pr` 23.75s → posted
  the comment on PR #1).
