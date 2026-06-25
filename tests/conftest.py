"""Shared pytest fixtures for the TypeWright test suite."""

import pytest
from fastapi.testclient import TestClient

from typewright.kestrel import SandboxResult
from typewright.main import (
    create_app,
    get_generate_strategies,
    get_generate_test_file,
    get_infer_properties,
    get_run_tests,
    get_suggest_fix,
)
from typewright.models import (
    DetectedProperty,
    GeneratedStrategy,
    GeneratedTestFile,
    ProposedFix,
    PropertyAnalysis,
    PropertyClass,
    StrategyPlan,
)

# Fixed results the default fakes return, so API tests never hit a real LLM or a live
# Kestrel (all four steps — detection, generation, test generation, sandbox execution —
# are injected via dependencies, D21, D28, D36, D41).
SAMPLE_ANALYSIS = PropertyAnalysis(
    detected=[
        DetectedProperty(
            property_class=PropertyClass.IDEMPOTENCE,
            relation="f(f(x)) == f(x)",
            rationale="normalizer — re-running changes nothing",
            confidence=0.9,
        )
    ],
    input_types={"x": "str"},
    return_type="str",
)

SAMPLE_PLAN = StrategyPlan(
    strategies=[
        GeneratedStrategy(
            argument="x",
            strategy="st.text()",
            rationale="any string is a valid input",
            confidence=0.9,
        )
    ],
    extra_imports=[],
)

SAMPLE_TEST_FILE = GeneratedTestFile(
    source=(
        "from hypothesis import given, strategies as st\n"
        "import pytest\n\n\n"
        "def f(x):\n    return x\n\n\n"
        "@given(x=st.text())\n"
        "def test_idempotence(x):\n    assert f(f(x)) == f(x)\n"
    ),
    test_names=["test_idempotence"],
    skipped=[],
)

# Default sandbox outcome: a clean run (all tests passed) -> no bugs.
SAMPLE_SANDBOX_RESULT = SandboxResult(
    stdout="1 passed in 0.05s",
    stderr="",
    exit_code=0,
    duration_ms=12,
    timed_out=False,
)

# Default proposed fix: same name/signature as the sample function, so build_fix_file splices it.
SAMPLE_PROPOSED_FIX = ProposedFix(
    corrected_source="def f(x):\n    return x",
    explanation="example fix",
)


def _default_infer(meta, *, model_tier=None) -> PropertyAnalysis:
    return SAMPLE_ANALYSIS


def _default_gen(meta, analysis, *, model_tier=None) -> StrategyPlan:
    return SAMPLE_PLAN


def _default_testgen(meta, analysis, plan, *, model_tier=None) -> GeneratedTestFile:
    return SAMPLE_TEST_FILE


def _default_run(test_file, *, timeout_seconds, settings=None) -> SandboxResult:
    return SAMPLE_SANDBOX_RESULT


def _default_suggest(meta, report, *, model_tier=None) -> ProposedFix:
    return SAMPLE_PROPOSED_FIX


@pytest.fixture
def make_client():
    """Factory for a TestClient whose five pipeline steps are all mocked.

    Detection, generation, test generation, AND sandbox execution are injected via
    dependencies (D21, D28, D36, D41); overriding them lets API tests run with no live key
    and no live Kestrel. Defaults return SAMPLE_ANALYSIS / SAMPLE_PLAN / SAMPLE_TEST_FILE /
    SAMPLE_SANDBOX_RESULT; pass a custom ``infer``, ``gen``, ``gen_tests``, or ``run`` to
    exercise tier selection, bug surfacing, timeouts, or pipeline failures.
    """
    clients: list[TestClient] = []

    def _make(
        infer=_default_infer,
        gen=_default_gen,
        gen_tests=_default_testgen,
        run=_default_run,
        suggest=_default_suggest,
    ) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_infer_properties] = lambda: infer
        app.dependency_overrides[get_generate_strategies] = lambda: gen
        app.dependency_overrides[get_generate_test_file] = lambda: gen_tests
        app.dependency_overrides[get_run_tests] = lambda: run
        app.dependency_overrides[get_suggest_fix] = lambda: suggest
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client) -> TestClient:
    """A TestClient with all four pipeline steps mocked (no live key, no live Kestrel)."""
    return make_client()