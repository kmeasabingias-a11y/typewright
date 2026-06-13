"""Shared pytest fixtures for the TypeWright test suite."""

import pytest
from fastapi.testclient import TestClient

from typewright.main import create_app, get_infer_properties
from typewright.models import DetectedProperty, PropertyAnalysis, PropertyClass

# A fixed analysis the default fake returns, so API tests never hit a real LLM
# (the detection step is injected via get_infer_properties — D21, D23).
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


def _default_infer(meta, *, model_tier=None) -> PropertyAnalysis:
    return SAMPLE_ANALYSIS


@pytest.fixture
def make_client():
    """Factory for a TestClient whose property detection is mocked.

    The route's LLM step is injected via ``get_infer_properties`` (D21); overriding
    that dependency lets API tests run with no live key. Defaults to a fake that
    returns ``SAMPLE_ANALYSIS``; pass a custom ``infer`` to exercise tier selection
    or pipeline failures.
    """
    clients: list[TestClient] = []

    def _make(infer=_default_infer) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_infer_properties] = lambda: infer
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client) -> TestClient:
    """A TestClient with property detection mocked (returns SAMPLE_ANALYSIS).

    Building the app per test (via the ``create_app()`` factory) keeps each test
    isolated from the others' state.
    """
    return make_client()
