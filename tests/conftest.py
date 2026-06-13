"""Shared pytest fixtures for the TypeWright test suite."""

import pytest
from fastapi.testclient import TestClient

from typewright.main import create_app, get_infer_contract
from typewright.models import Contract

# A fixed contract the default fake returns, so API tests never hit a real LLM
# (the inference step is injected via get_infer_contract — D21).
SAMPLE_CONTRACT = Contract(
    preconditions=["inputs are valid"],
    postconditions=["returns a result"],
    invariants=["inputs are not mutated"],
)


def _default_infer(meta, *, model_tier=None) -> Contract:
    return SAMPLE_CONTRACT


@pytest.fixture
def make_client():
    """Factory for a TestClient whose contract inference is mocked.

    The route's LLM step is injected via ``get_infer_contract`` (D21); overriding
    that dependency lets API tests run with no live key. Defaults to a fake that
    returns ``SAMPLE_CONTRACT``; pass a custom ``infer`` to exercise tier
    selection or pipeline failures.
    """
    clients: list[TestClient] = []

    def _make(infer=_default_infer) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_infer_contract] = lambda: infer
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client) -> TestClient:
    """A TestClient with contract inference mocked (returns SAMPLE_CONTRACT).

    Building the app per test (via the ``create_app()`` factory) keeps each test
    isolated from the others' state.
    """
    return make_client()