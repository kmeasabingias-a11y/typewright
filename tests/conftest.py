"""Shared pytest fixtures for the TypeWright test suite."""

import pytest
from fastapi.testclient import TestClient

from typewright.main import create_app, get_generate_strategies, get_infer_properties
from typewright.models import (
    DetectedProperty,
    GeneratedStrategy,
    PropertyAnalysis,
    PropertyClass,
    StrategyPlan,
)

# Fixed results the default fakes return, so API tests never hit a real LLM (both
# the detection and generation steps are injected via dependencies — D21, D28).
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


def _default_infer(meta, *, model_tier=None) -> PropertyAnalysis:
    return SAMPLE_ANALYSIS


def _default_gen(meta, analysis, *, model_tier=None) -> StrategyPlan:
    return SAMPLE_PLAN


@pytest.fixture
def make_client():
    """Factory for a TestClient whose detection AND generation are mocked.

    Both LLM steps are injected via dependencies (D21, D28); overriding them lets
    API tests run with no live key. Defaults return ``SAMPLE_ANALYSIS`` /
    ``SAMPLE_PLAN``; pass a custom ``infer`` or ``gen`` to exercise tier selection
    or pipeline failures.
    """
    clients: list[TestClient] = []

    def _make(infer=_default_infer, gen=_default_gen) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_infer_properties] = lambda: infer
        app.dependency_overrides[get_generate_strategies] = lambda: gen
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client) -> TestClient:
    """A TestClient with both LLM steps mocked (SAMPLE_ANALYSIS + SAMPLE_PLAN).

    Building the app per test (via the ``create_app()`` factory) keeps each test
    isolated from the others' state.
    """
    return make_client()