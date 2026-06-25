"""Tests for the GitHub webhook (Phase 7): signature verification, event parsing, the route."""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from typewright import webhook
from typewright.config import Settings, get_settings
from typewright.main import create_app, get_enqueue
from typewright.models import PullRequestJob


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _pr_payload(action="opened"):
    return {
        "action": action,
        "repository": {"full_name": "octo/repo"},
        "pull_request": {"number": 7, "head": {"sha": "abc123def4567890"}},
        "installation": {"id": 42},
    }


# --- verify_signature (pure) ---

def test_verify_signature_accepts_valid():
    body = b'{"hello":"world"}'
    assert webhook.verify_signature(body, _sign(body, "s3cret"), "s3cret") is True


def test_verify_signature_rejects_wrong_secret():
    body = b'{"hello":"world"}'
    assert webhook.verify_signature(body, _sign(body, "other"), "s3cret") is False


def test_verify_signature_rejects_missing_or_malformed():
    assert webhook.verify_signature(b"x", None, "s") is False
    assert webhook.verify_signature(b"x", "deadbeef", "s") is False  # no sha256= prefix


# --- parse_pull_request_event (pure) ---

def test_parse_actionable_actions_yield_job():
    for action in ("opened", "synchronize", "reopened"):
        job = webhook.parse_pull_request_event("pull_request", _pr_payload(action))
        assert isinstance(job, PullRequestJob)
        assert (job.repo_full_name, job.pr_number, job.head_sha, job.installation_id) == (
            "octo/repo",
            7,
            "abc123def4567890",
            42,
        )


def test_parse_ignores_non_actionable_action():
    assert webhook.parse_pull_request_event("pull_request", _pr_payload("closed")) is None


def test_parse_ignores_other_event_types():
    assert webhook.parse_pull_request_event("issues", _pr_payload("opened")) is None


def test_parse_missing_fields_returns_none():
    assert webhook.parse_pull_request_event("pull_request", {"action": "opened"}) is None


# --- the route ---

def _client(secret="whsec", capture=None):
    app = create_app()
    settings = Settings(_env_file=None, github_webhook_secret=secret)
    app.dependency_overrides[get_settings] = lambda: settings
    if capture is not None:
        async def fake_enqueue(job):
            capture.append(job)
        app.dependency_overrides[get_enqueue] = lambda: fake_enqueue
    return TestClient(app)


def test_webhook_queues_on_valid_signature_and_opened():
    captured = []
    client = _client(secret="whsec", capture=captured)
    body = json.dumps(_pr_payload("opened")).encode()
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body, "whsec"),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 202
    assert resp.json()["pr"] == 7
    assert len(captured) == 1 and captured[0].pr_number == 7


def test_webhook_rejects_bad_signature():
    captured = []
    client = _client(secret="whsec", capture=captured)
    body = json.dumps(_pr_payload()).encode()
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body, "WRONG")},
    )
    assert resp.status_code == 403
    assert captured == []


def test_webhook_ignores_ping_event():
    captured = []
    client = _client(secret="whsec", capture=captured)
    body = json.dumps({"zen": "Keep it simple"}).encode()
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sign(body, "whsec")},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert captured == []


def test_webhook_skips_verification_when_no_secret():
    captured = []
    client = _client(secret=None, capture=captured)  # no secret -> verification skipped (dev)
    body = json.dumps(_pr_payload("opened")).encode()
    resp = client.post(
        "/webhook/github", content=body, headers={"X-GitHub-Event": "pull_request"}
    )
    assert resp.status_code == 202
    assert len(captured) == 1