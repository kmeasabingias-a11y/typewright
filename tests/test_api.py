"""Tests for the HTTP surface (``POST /v1/analyze`` and ``/health``).

These drive the real FastAPI app through FastAPI's TestClient, asserting the
status-code contract (200 success, 400 caller error, 422 malformed request,
500 pipeline failure) and that the response carries only the honest Phase 2
subset: function + properties (DECISIONS.md D5, D8, D21, D23). Property detection
is mocked via the ``get_infer_properties`` dependency (conftest), so no live LLM
key is needed.
"""

from typewright.errors import PipelineError
from typewright.models import BugVerdict, GeneratedTestFile, ProposedFix, PropertyAnalysis, StrategyPlan
from typewright.kestrel import SandboxResult


def test_health_returns_ok(client):
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_analyze_returns_parsed_function(client):
    code = "def add(a: int, b: int) -> int:\n    return a + b"
    resp = client.post("/v1/analyze", json={"code": code})

    assert resp.status_code == 200
    body = resp.json()
    assert "analysis_id" in body
    fn = body["function"]
    assert fn["name"] == "add"
    assert fn["signature"] == "(a: int, b: int) -> int"
    assert fn["return_type"] == "int"
    assert [arg["name"] for arg in fn["args"]] == ["a", "b"]


def test_analyze_returns_only_the_honest_subset(client):
    """Phase 9 returns function + properties + strategy_plan + test_file + bugs_found +
    fix_suggestion (null unless requested) + metadata (real as of Phase 9, D51)."""
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    body = resp.json()
    assert set(body.keys()) == {
        "analysis_id",
        "function",
        "properties",
        "strategy_plan",
        "test_file",
        "bugs_found",
        "fix_suggestion",
        "metadata",
        "unavailable_imports",
    }
    assert set(body["properties"].keys()) == {
        "detected",
        "input_types",
        "return_type",
    }
    assert set(body["strategy_plan"].keys()) == {"strategies", "extra_imports"}
    assert set(body["test_file"].keys()) == {"source", "test_names", "skipped"}
    assert set(body["metadata"].keys()) == {
        "analysis_duration_ms",
        "llm_cost_usd",
        "tests_generated",
        "tests_run",
        "hypothesis_examples_tried",
    }
    assert body["fix_suggestion"] is None  # not requested -> null


def test_analyze_includes_detected_properties(client):
    """The mocked detection result is surfaced in the response."""
    resp = client.post(
        "/v1/analyze", json={"code": "def add(a, b):\n    return a + b"}
    )

    assert resp.status_code == 200
    properties = resp.json()["properties"]
    assert properties["detected"][0]["property_class"] == "idempotence"
    assert properties["detected"][0]["confidence"] == 0.9
    assert properties["return_type"] == "str"


def test_model_tier_is_passed_to_inference(make_client):
    """The request's model_tier reaches the detection step verbatim."""
    seen = {}

    def infer(meta, *, model_tier=None):
        seen["tier"] = model_tier
        return PropertyAnalysis()

    client = make_client(infer)
    resp = client.post(
        "/v1/analyze",
        json={"code": "def f():\n    pass", "model_tier": "premium"},
    )

    assert resp.status_code == 200
    assert seen["tier"] == "premium"


def test_analyze_with_function_name(client):
    code = "def a():\n    pass\n\ndef b(x: str) -> str:\n    return x"
    resp = client.post("/v1/analyze", json={"code": code, "function_name": "b"})

    assert resp.status_code == 200
    assert resp.json()["function"]["name"] == "b"


def test_analysis_id_is_unique_per_call(client):
    code = "def f():\n    pass"
    first = client.post("/v1/analyze", json={"code": code}).json()["analysis_id"]
    second = client.post("/v1/analyze", json={"code": code}).json()["analysis_id"]

    assert first != second


def test_analyze_includes_generated_strategies(client):
    """The mocked strategy plan is surfaced in the response."""
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})

    assert resp.status_code == 200
    plan = resp.json()["strategy_plan"]
    assert plan["strategies"][0]["argument"] == "x"
    assert plan["strategies"][0]["strategy"] == "st.text()"


def test_model_tier_is_passed_to_generation(make_client):
    """The request's model_tier reaches the generation step verbatim."""
    seen = {}

    def gen(meta, analysis, *, model_tier=None):
        seen["tier"] = model_tier
        return StrategyPlan()

    client = make_client(gen=gen)
    resp = client.post(
        "/v1/analyze", json={"code": "def f():\n    pass", "model_tier": "premium"}
    )

    assert resp.status_code == 200
    assert seen["tier"] == "premium"


def test_generation_failure_is_500_with_stage(make_client):
    """A PipelineError from generation becomes a 500 naming the failing stage (D15, D30)."""

    def gen(meta, analysis, *, model_tier=None):
        raise PipelineError("strategy_generation", "model unavailable")

    client = make_client(gen=gen)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["stage"] == "strategy_generation"
    assert "strategy_generation" in body["detail"]


def test_analyze_includes_test_file(client):
    """The mocked generated test file is surfaced in the response."""
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})

    assert resp.status_code == 200
    test_file = resp.json()["test_file"]
    assert "def test_idempotence" in test_file["source"]
    assert test_file["test_names"] == ["test_idempotence"]


def test_model_tier_is_passed_to_testgen(make_client):
    """The request's model_tier reaches the test-generation step verbatim."""
    seen = {}

    def gen_tests(meta, analysis, plan, *, model_tier=None):
        seen["tier"] = model_tier
        return GeneratedTestFile(source="")

    client = make_client(gen_tests=gen_tests)
    resp = client.post(
        "/v1/analyze", json={"code": "def f():\n    pass", "model_tier": "premium"}
    )

    assert resp.status_code == 200
    assert seen["tier"] == "premium"


def test_testgen_failure_is_500_with_stage(make_client):
    """A PipelineError from test generation becomes a 500 naming the failing stage (D15, D36)."""

    def gen_tests(meta, analysis, plan, *, model_tier=None):
        raise PipelineError("test_generation", "model unavailable")

    client = make_client(gen_tests=gen_tests)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["stage"] == "test_generation"
    assert "test_generation" in body["detail"]


def test_analyze_includes_bugs_found(client):
    """A clean sandbox run (default fake) surfaces an empty bugs_found list (Phase 5, D41)."""
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})

    assert resp.status_code == 200
    assert resp.json()["bugs_found"] == []


def test_bugs_found_surfaces_sandbox_failures(make_client):
    """A failing run is parsed into structured bugs in the response (D40, D41)."""
    failing = SandboxResult(
        stdout=(
            "F\n"
            "Falsifying example: test_idempotence(x='A')\n"
            "FAILED main.py::test_idempotence - assert 'AA' == 'A'\n"
            "1 failed in 0.10s\n"
        ),
        stderr="",
        exit_code=1,
        duration_ms=30,
        timed_out=False,
    )

    client = make_client(run=lambda test_file, *, timeout_seconds, settings=None: failing)
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})

    assert resp.status_code == 200
    bugs = resp.json()["bugs_found"]
    assert len(bugs) == 1
    assert bugs[0]["test_name"] == "test_idempotence"
    assert bugs[0]["failing_input"] == "x='A'"
    assert bugs[0]["severity"] == "property_violation"
    assert bugs[0]["violated_property"] == "f(f(x)) == f(x)"  # mapped from SAMPLE_ANALYSIS


def test_max_test_runtime_seconds_passed_through(make_client):
    """The request's max_test_runtime_seconds reaches the sandbox step as the budget."""
    seen = {}

    def run(test_file, *, timeout_seconds, settings=None):
        seen["budget"] = timeout_seconds
        return SandboxResult(
            stdout="1 passed", stderr="", exit_code=0, duration_ms=5, timed_out=False
        )

    client = make_client(run=run)
    resp = client.post(
        "/v1/analyze",
        json={"code": "def f():\n    pass", "max_test_runtime_seconds": 12.5},
    )

    assert resp.status_code == 200
    assert seen["budget"] == 12.5


def test_timeout_is_504(make_client):
    """A timed-out sandbox run becomes a 504 (D42), not a false-clean 200."""
    timed_out = SandboxResult(
        stdout="", stderr="", exit_code=-1, duration_ms=30000, timed_out=True
    )

    client = make_client(run=lambda test_file, *, timeout_seconds, settings=None: timed_out)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    assert resp.status_code == 504
    assert "budget" in resp.json()["detail"]

def test_sandbox_failure_is_500_with_stage(make_client):
    """A PipelineError from the sandbox step becomes a 500 naming the stage (D15, D37)."""

    def run(test_file, *, timeout_seconds, settings=None):
        raise PipelineError("sandbox_execution", "Kestrel unreachable")

    client = make_client(run=run)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["stage"] == "sandbox_execution"
    assert "sandbox_execution" in body["detail"]


# --- Phase 6: fix suggestion (opt-in, verified by a re-run; D44/D45) ---------

_FAILING_RUN = SandboxResult(
    stdout=(
        "Falsifying example: test_idempotence(x='A')\n"
        "FAILED main.py::test_idempotence - assert 'AA' == 'A'\n"
        "1 failed in 0.10s\n"
    ),
    stderr="",
    exit_code=1,
    duration_ms=30,
    timed_out=False,
)
_CLEAN_RUN = SandboxResult(
    stdout="1 passed in 0.05s", stderr="", exit_code=0, duration_ms=5, timed_out=False
)


def test_fix_suggestion_absent_by_default(client):
    """Without include_fix_suggestion, no fix is attempted (opt-in, D44)."""
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})
    assert resp.status_code == 200
    assert resp.json()["fix_suggestion"] is None


def test_fix_suggestion_skipped_when_no_bugs(make_client):
    """Requested but a clean run -> nothing to fix, and suggest is never called (D44)."""
    called = {"suggest": False}

    def suggest(meta, report, *, model_tier=None):
        called["suggest"] = True
        return ProposedFix(corrected_source="def f(x):\n    return x", explanation="x")

    client = make_client(suggest=suggest)  # default run is clean -> no bugs
    resp = client.post(
        "/v1/analyze",
        json={"code": "def f(x):\n    return x", "include_fix_suggestion": True},
    )
    assert resp.status_code == 200
    assert resp.json()["fix_suggestion"] is None
    assert called["suggest"] is False


def test_fix_suggestion_verified_when_rerun_green(make_client):
    """Bugs found, fix proposed, the verification re-run is green -> verified=True (D45)."""
    calls = {"n": 0}

    def run(test_file, *, timeout_seconds, settings=None):
        calls["n"] += 1
        return _FAILING_RUN if calls["n"] == 1 else _CLEAN_RUN

    def suggest(meta, report, *, model_tier=None):
        return ProposedFix(corrected_source="def f(x):\n    return x", explanation="guarded")

    client = make_client(run=run, suggest=suggest)
    resp = client.post(
        "/v1/analyze",
        json={"code": "def f(x):\n    return x", "include_fix_suggestion": True},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["bugs_found"]) == 1  # the original bug is still reported
    fix = body["fix_suggestion"]
    assert fix is not None
    assert fix["verified"] is True
    assert fix["code"] == "def f(x):\n    return x"
    assert calls["n"] == 2  # one run to find bugs, one to verify the fix


def test_fix_suggestion_unverified_when_rerun_still_fails(make_client):
    """The re-run still fails -> 'no confident fix' (verified=False), request still 200 (D44)."""

    def suggest(meta, report, *, model_tier=None):
        return ProposedFix(corrected_source="def f(x):\n    return x", explanation="attempt")

    client = make_client(
        run=lambda tf, *, timeout_seconds, settings=None: _FAILING_RUN, suggest=suggest
    )
    resp = client.post(
        "/v1/analyze",
        json={"code": "def f(x):\n    return x", "include_fix_suggestion": True},
    )

    assert resp.status_code == 200
    assert resp.json()["fix_suggestion"]["verified"] is False


def test_fix_generation_failure_degrades_not_500(make_client):
    """A fix-gen PipelineError degrades to no suggestion — it does NOT sink the analysis (D44)."""

    def suggest(meta, report, *, model_tier=None):
        raise PipelineError("fix_suggestion", "model unavailable")

    client = make_client(
        run=lambda tf, *, timeout_seconds, settings=None: _FAILING_RUN, suggest=suggest
    )
    resp = client.post(
        "/v1/analyze",
        json={"code": "def f(x):\n    return x", "include_fix_suggestion": True},
    )

    assert resp.status_code == 200  # best-effort: the optional step failing is not a 500
    body = resp.json()
    assert len(body["bugs_found"]) == 1  # the real analysis is intact
    assert body["fix_suggestion"] is None


def test_model_tier_is_passed_to_fix(make_client):
    """The request's model_tier reaches the fix step verbatim."""
    seen = {}

    def suggest(meta, report, *, model_tier=None):
        seen["tier"] = model_tier
        return ProposedFix(corrected_source="def f(x):\n    return x", explanation="x")

    client = make_client(
        run=lambda tf, *, timeout_seconds, settings=None: _FAILING_RUN, suggest=suggest
    )
    resp = client.post(
        "/v1/analyze",
        json={
            "code": "def f(x):\n    return x",
            "include_fix_suggestion": True,
            "model_tier": "premium",
        },
    )

    assert resp.status_code == 200
    assert seen["tier"] == "premium"


# --- Phase 10: bug verification (second-opinion precision filter; D60) -------


def test_verification_attached_to_bugs(make_client):
    """With verification on (default), each surfaced bug carries a BugVerdict (D60)."""
    client = make_client(run=lambda tf, *, timeout_seconds, settings=None: _FAILING_RUN)
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})

    assert resp.status_code == 200
    bug = resp.json()["bugs_found"][0]
    assert bug["verification"] is not None
    assert bug["verification"]["property_is_contractual"] is True
    assert bug["verification"]["input_in_domain"] is True


def test_verification_demotes_over_inference(make_client):
    """A 'not contractual' verdict stays attached (the bug is demoted, never dropped — D60)."""
    over_inferred = BugVerdict(
        property_is_contractual=False, input_in_domain=True, reasoning="never promised"
    )
    client = make_client(
        run=lambda tf, *, timeout_seconds, settings=None: _FAILING_RUN,
        verify=lambda meta, detected, bug, *, model_tier=None: over_inferred,
    )
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})

    assert resp.status_code == 200
    bugs = resp.json()["bugs_found"]
    assert len(bugs) == 1  # not dropped — still reported, just demoted
    assert bugs[0]["verification"]["property_is_contractual"] is False


def test_verification_can_be_disabled_per_request(make_client):
    """verify_findings=false skips verification entirely; the verdict stays null (D60)."""
    calls = {"n": 0}

    def verify(meta, detected, bug, *, model_tier=None):
        calls["n"] += 1
        return BugVerdict(property_is_contractual=True, input_in_domain=True, reasoning="x")

    client = make_client(
        run=lambda tf, *, timeout_seconds, settings=None: _FAILING_RUN, verify=verify
    )
    resp = client.post(
        "/v1/analyze", json={"code": "def f(x):\n    return x", "verify_findings": False}
    )

    assert resp.status_code == 200
    assert resp.json()["bugs_found"][0]["verification"] is None
    assert calls["n"] == 0  # the verify step was never called


def test_verification_failure_degrades_not_500(make_client):
    """A verification error leaves the bug unverified; the analysis stays valid (D44/D60)."""

    def verify(meta, detected, bug, *, model_tier=None):
        raise PipelineError("bug_verification", "judge unreachable")

    client = make_client(
        run=lambda tf, *, timeout_seconds, settings=None: _FAILING_RUN, verify=verify
    )
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})

    assert resp.status_code == 200  # not a 500 — verification is best-effort
    bugs = resp.json()["bugs_found"]
    assert len(bugs) == 1  # the real analysis is intact
    assert bugs[0]["verification"] is None  # left unverified


# --- Phase 10: sandbox dependency handling (stdlib carried, third-party honest; D61) ---


def test_unavailable_import_reported_and_sandbox_skipped(make_client):
    """A function needing a package the sandbox lacks: honest note, sandbox skipped, no bug (D61)."""
    calls = {"n": 0}

    def run(test_file, *, timeout_seconds, settings=None):
        calls["n"] += 1
        return SandboxResult(stdout="1 passed", stderr="", exit_code=0, duration_ms=1, timed_out=False)

    client = make_client(run=run)
    resp = client.post(
        "/v1/analyze",
        json={"code": "import tensorflow as tf\n\ndef f(x):\n    return tf.abs(x)"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["unavailable_imports"] == ["tensorflow"]
    assert body["bugs_found"] == []  # never a phantom crash
    assert calls["n"] == 0  # the sandbox was skipped


def test_stdlib_import_is_available_and_runs(make_client):
    """A stdlib import is available, so the sandbox runs and unavailable_imports is empty (D61)."""
    calls = {"n": 0}

    def run(test_file, *, timeout_seconds, settings=None):
        calls["n"] += 1
        return SandboxResult(stdout="1 passed in 0.05s", stderr="", exit_code=0, duration_ms=1, timed_out=False)

    client = make_client(run=run)
    resp = client.post(
        "/v1/analyze",
        json={"code": "import re\n\ndef f(s):\n    return re.sub('a', 'b', s)"},
    )

    assert resp.status_code == 200
    assert resp.json()["unavailable_imports"] == []
    assert calls["n"] == 1  # stdlib available -> sandbox ran


# --- Caller errors map to 400 (the TypeWrightError family) ------------------


def test_syntax_error_is_400(client):
    resp = client.post("/v1/analyze", json={"code": "def f(:"})

    assert resp.status_code == 400
    assert "valid Python" in resp.json()["detail"]


def test_ambiguous_function_is_400(client):
    code = "def a():\n    pass\n\ndef b():\n    pass"
    resp = client.post("/v1/analyze", json={"code": code})

    assert resp.status_code == 400
    assert "function_name" in resp.json()["detail"]


def test_no_function_is_400(client):
    resp = client.post("/v1/analyze", json={"code": "x = 1"})

    assert resp.status_code == 400


def test_unknown_function_name_is_400(client):
    code = "def a():\n    pass"
    resp = client.post("/v1/analyze", json={"code": code, "function_name": "zzz"})

    assert resp.status_code == 400


# --- Malformed request shape is 422 (FastAPI validation, not our 400) -------


def test_missing_code_field_is_422(client):
    resp = client.post("/v1/analyze", json={})

    assert resp.status_code == 422


# --- Pipeline failure (our fault, not the caller's) is 500 with the stage -----


def test_pipeline_failure_is_500_with_stage(make_client):
    """A PipelineError from detection becomes a 500 naming the failing stage (D15)."""

    def infer(meta, *, model_tier=None):
        raise PipelineError("property_detection", "model unavailable")

    client = make_client(infer)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["stage"] == "property_detection"
    assert "property_detection" in body["detail"]


def test_analyze_persists_run_and_it_is_fetchable(client):
    code = "def add(a: int, b: int) -> int:\n    return a + b"
    posted = client.post("/v1/analyze", json={"code": code})
    assert posted.status_code == 200
    rid = posted.json()["analysis_id"]

    got = client.get(f"/v1/runs/{rid}")
    assert got.status_code == 200
    assert got.json() == posted.json()


def test_get_unknown_run_is_404(client):
    assert client.get("/v1/runs/does-not-exist").status_code == 404


def test_persist_failure_does_not_fail_analysis(make_client):
    class BoomStore:
        def save(self, response):
            raise RuntimeError("disk full")

        def load(self, analysis_id):
            return None

    client = make_client(store=BoomStore())
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})
    assert resp.status_code == 200  # best-effort: a save failure must not sink the analysis


def test_analyze_includes_metadata(client):
    """metadata is populated; on the mocked path cost is 0.0 and counts come from the canned data."""
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})
    meta = resp.json()["metadata"]
    assert meta["tests_generated"] == 1            # SAMPLE_TEST_FILE has one test
    assert meta["tests_run"] == 1                  # SAMPLE_SANDBOX_RESULT: "1 passed"
    assert meta["llm_cost_usd"] == 0.0             # steps mocked -> no real LLM call billed
    assert meta["analysis_duration_ms"] >= 0
    assert meta["hypothesis_examples_tried"] is None


def test_over_budget_returns_402(make_client):
    from typewright.errors import CostBudgetExceededError

    def over_budget(meta, *, model_tier=None):
        raise CostBudgetExceededError(0.51, 0.50)

    client = make_client(infer=over_budget)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})
    assert resp.status_code == 402
    body = resp.json()
    assert body["limit_usd"] == 0.50
    assert body["spent_usd"] == 0.51


def test_request_budget_lowers_the_ceiling(make_client):
    from typewright import metrics

    class _Raw:
        _hidden_params = {"response_cost": 0.02}

    def spend(meta, *, model_tier=None):
        metrics.add_cost(_Raw())  # 0.02 spent inside the route's cost_scope
        return None  # never reached — add_cost raises once over the 0.01 budget

    client = make_client(infer=spend)
    resp = client.post(
        "/v1/analyze", json={"code": "def f():\n    pass", "max_cost_usd": 0.01}
    )
    assert resp.status_code == 402


def test_rate_limited_returns_429(make_client):
    from typewright.ratelimit import RateLimitResult

    class _Blocked:
        def check(self, key, limit, window_seconds):
            return RateLimitResult(allowed=False, retry_after=42)

    client = make_client(rate_limiter=_Blocked())
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "42"
    assert resp.json()["retry_after"] == 42


def test_analyze_emits_a_trace(client, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="typewright"):
        resp = client.post("/v1/analyze", json={"code": "def add(a, b):\n    return a + b"})
    assert resp.status_code == 200
    assert any("event=analysis_trace" in r.getMessage() for r in caplog.records)


def test_sandbox_unavailable_returns_503(make_client):
    from typewright.errors import SandboxUnavailableError

    def unavailable(test_file, *, timeout_seconds, settings=None):
        raise SandboxUnavailableError(retry_after=5)

    client = make_client(run=unavailable)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "5"


def test_oversized_code_is_422(client):
    resp = client.post("/v1/analyze", json={"code": "x" * 100_001})
    assert resp.status_code == 422


def test_monthly_budget_exhausted_returns_503(make_client):
    from typewright.errors import MonthlyBudgetExceededError

    def exhausted(meta, *, model_tier=None):
        raise MonthlyBudgetExceededError(spent_usd=10.5, limit_usd=10.0, retry_after=3600)

    client = make_client(infer=exhausted)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "3600"
    assert resp.json()["limit_usd"] == 10.0