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