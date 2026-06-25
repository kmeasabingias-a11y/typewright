"""Tests for the PR worker orchestration (Phase 7). GitHub + analysis mocked; no network/Redis."""

from typewright import worker
from typewright.config import Settings
from typewright.errors import PipelineError
from typewright.models import Bug, BugSeverity, FunctionFinding, PullRequestJob

_PATCH = "@@ -1,2 +1,2 @@\n def absolute(x):\n-    return abs(x)\n+    return x\n"
_CONTENT = "def absolute(x):\n    return x\n"


def _job():
    return PullRequestJob(repo_full_name="octo/repo", pr_number=7, head_sha="abc1234", installation_id=42)


def _bug():
    return Bug(test_name="t", failing_input="x=-1", error="AssertionError",
                violated_property="absolute(x) >= 0", severity=BugSeverity.PROPERTY_VIOLATION)


def _wire(monkeypatch, files, analyze):
    monkeypatch.setattr(worker, "installation_token", lambda iid, s: "tok")
    monkeypatch.setattr(worker, "list_pr_files", lambda repo, pr, tok: files)
    monkeypatch.setattr(worker, "get_file_content", lambda repo, path, ref, tok: _CONTENT)
    monkeypatch.setattr(worker, "analyze_one", analyze)
    posted = {}
    monkeypatch.setattr(worker, "post_comment", lambda repo, pr, body, tok: posted.update(pr=pr, body=body))
    return posted


def test_process_pr_comments_when_bugs(monkeypatch):
    files = [{"filename": "absolute.py", "status": "modified", "patch": _PATCH}]
    posted = _wire(monkeypatch, files, lambda meta, s=None: FunctionFinding(function_name="absolute",
bugs=[_bug()]))
    worker.process_pr(_job(), Settings(_env_file=None))
    assert posted["pr"] == 7 and "x=-1" in posted["body"]


def test_process_pr_skips_non_python_and_removed(monkeypatch):
    files = [
        {"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n"},
        {"filename": "gone.py", "status": "removed", "patch": None},
    ]
    posted = _wire(monkeypatch, files, lambda meta, s=None: FunctionFinding(function_name="x", bugs=[_bug()]))
    worker.process_pr(_job(), Settings(_env_file=None))
    assert posted == {}  # nothing analyzable -> no comment


def test_process_pr_no_comment_when_clean(monkeypatch):
    files = [{"filename": "absolute.py", "status": "modified", "patch": _PATCH}]
    posted = _wire(monkeypatch, files, lambda meta, s=None: FunctionFinding(function_name="absolute", bugs=[]))
    worker.process_pr(_job(), Settings(_env_file=None))
    assert posted == {}  # clean -> no comment


def test_process_pr_skips_function_on_error(monkeypatch):
    files = [{"filename": "absolute.py", "status": "modified", "patch": _PATCH}]
    def boom(meta, s=None):
        raise PipelineError("inference", "model down")
    posted = _wire(monkeypatch, files, boom)
    worker.process_pr(_job(), Settings(_env_file=None))  # must not raise
    assert posted == {}