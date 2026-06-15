"""Shared pytest fixtures for the TypeWright test suite."""

import pytest
from fastapi.testclient import TestClient

from typewright.main import (
    create_app,
    get_generate_strategies,
    get_generate_test_file,
    get_infer_properties,
)
from typewright.models import (
    DetectedProperty,
    GeneratedStrategy,
    GeneratedTestFile,
    PropertyAnalysis,
    PropertyClass,
    StrategyPlan,
)

# Fixed results the default fakes return, so API tests never hit a real LLM (all three
# steps — detection, generation, test generation — are injected via dependencies, D21,
# D28, D36).
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


def _default_infer(meta, *, model_tier=None) -> PropertyAnalysis:
    return SAMPLE_ANALYSIS


def _default_gen(meta, analysis, *, model_tier=None) -> StrategyPlan:
    return SAMPLE_PLAN


def _default_testgen(meta, analysis, plan, *, model_tier=None) -> GeneratedTestFile:
    return SAMPLE_TEST_FILE


@pytest.fixture
def make_client():
    """Factory for a TestClient whose three LLM steps are all mocked.

    Detection, generation, AND test generation are injected via dependencies (D21,
    D28, D36); overriding them lets API tests run with no live key. Defaults return
    ``SAMPLE_ANALYSIS`` / ``SAMPLE_PLAN`` / ``SAMPLE_TEST_FILE``; pass a custom
    ``infer``, ``gen``, or ``gen_tests`` to exercise tier selection or pipeline
    failures.
    """
    clients: list[TestClient] = []

    def _make(infer=_default_infer, gen=_default_gen, gen_tests=_default_testgen) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_infer_properties] = lambda: infer
        app.dependency_overrides[get_generate_strategies] = lambda: gen
        app.dependency_overrides[get_generate_test_file] = lambda: gen_tests
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client) -> TestClient:
    """A TestClient with all three LLM steps mocked (SAMPLE_ANALYSIS + SAMPLE_PLAN +
    SAMPLE_TEST_FILE).

    Building the app per test (via the ``create_app()`` factory) keeps each test
    isolated from the others' state.
    """
    return make_client()