"""Tests for the HTTP surface (``POST /v1/analyze`` and ``/health``).

These drive the real FastAPI app through FastAPI's TestClient, asserting the
status-code contract (200 success, 400 caller error, 422 malformed request,
500 pipeline failure) and that the response carries only the honest Phase 2
subset: function + properties (DECISIONS.md D5, D8, D21, D23). Property detection
is mocked via the ``get_infer_properties`` dependency (conftest), so no live LLM
key is needed.
"""

from typewright.errors import PipelineError
from typewright.models import PropertyAnalysis, StrategyPlan


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
    """Phase 3 returns analysis_id + function + properties + strategy_plan — not bugs/fix yet."""
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    body = resp.json()
    assert set(body.keys()) == {"analysis_id", "function", "properties", "strategy_plan"}
    assert set(body["properties"].keys()) == {
        "detected",
        "input_types",
        "return_type",
    }
    assert set(body["strategy_plan"].keys()) == {"strategies", "extra_imports"}
    assert "bugs_found" not in body
    assert "fix_suggestion" not in body


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
