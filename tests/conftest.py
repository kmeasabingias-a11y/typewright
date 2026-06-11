"""Shared pytest fixtures for the TypeWright test suite."""

import pytest
from fastapi.testclient import TestClient

from typewright.main import create_app


@pytest.fixture
def client() -> TestClient:
    """A TestClient wrapping a fresh, fully-configured app instance.

    Building the app per test (via the ``create_app()`` factory) keeps each test
    isolated from the others' state.
    """
    return TestClient(create_app())