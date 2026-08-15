"""Tests for the Phase 8 web demo page (GET /, D49)."""

from fastapi.testclient import TestClient

from typewright.main import create_app


def test_index_is_served_as_html():
    """GET / returns the demo page as HTML (no auth, no pipeline deps)."""
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_index_wires_to_the_real_endpoint():
    """The page must call the real endpoint, opt into a fix, and expose its controls."""
    body = TestClient(create_app()).get("/").text
    assert "/v1/analyze" in body
    assert "include_fix_suggestion" in body
    assert 'id="code"' in body
    assert 'id="analyze"' in body


def test_index_supports_shared_links():
    """The page can load a shared result (GET /v1/runs/) and offers a share bar."""
    body = TestClient(create_app()).get("/").text
    assert "/v1/runs/" in body
    assert 'id="share"' in body


def test_index_shows_inferred_property_disclaimer():
    body = TestClient(create_app()).get("/").text
    assert "AI-inferred properties" in body

def test_index_surfaces_unavailable_imports_and_the_access_gate(client):
    """Phase-10 framing pass + D62: the page renders honest degradation and forwards the code."""
    body = client.get("/").text
    assert "unavailable_imports" in body       # sandbox-skipped runs are shown, not silently empty
    assert "X-Demo-Access-Code" in body        # forwards ?code= to the gated analyze endpoint
    assert "How reliable is this?" in body     # the precision/limits note
