"""Tests for sandbox wrapping + execution dispatch (Phase 5, Unit 1).

``wrap_for_sandbox`` must produce a valid, sandbox-ready file; ``run_tests`` must wrap
then delegate to ``run_in_sandbox`` with the budget threaded through. The sandbox call
is faked by monkeypatching ``execution.run_in_sandbox`` — no live Kestrel (D38).
"""

import ast

from typewright import execution
from typewright.kestrel import SandboxResult
from typewright.models import GeneratedTestFile

_SAMPLE_SOURCE = (
    "from hypothesis import given, strategies as st\n"
    "import pytest\n\n\n"
    "def f(x):\n    return x\n\n\n"
    "@given(x=st.text())\n"
    "def test_idempotence(x):\n    assert f(f(x)) == f(x)\n"
)


def test_wrap_for_sandbox_is_valid_python_with_preamble_and_runner():
    wrapped = execution.wrap_for_sandbox(_SAMPLE_SOURCE)

    ast.parse(wrapped)  # raises if the wrapped file is not valid Python
    assert 'os.chdir("/tmp")' in wrapped
    assert "database=None" in wrapped
    assert "deadline=None" in wrapped
    assert "pytest.main([__file__" in wrapped


def test_wrap_for_sandbox_preserves_the_original_file():
    wrapped = execution.wrap_for_sandbox(_SAMPLE_SOURCE)

    assert "def f(x):" in wrapped
    assert "def test_idempotence(x):" in wrapped


def test_run_tests_wraps_then_submits(monkeypatch):
    captured = {}

    def fake_run(code, *, timeout_seconds, settings=None):
        captured["code"] = code
        captured["timeout_seconds"] = timeout_seconds
        return SandboxResult(
            stdout="1 passed", stderr="", exit_code=0, duration_ms=8, timed_out=False
        )

    monkeypatch.setattr(execution, "run_in_sandbox", fake_run)
    test_file = GeneratedTestFile(source=_SAMPLE_SOURCE, test_names=["test_idempotence"])
    result = execution.run_tests(test_file, timeout_seconds=25.0)

    assert result.exit_code == 0
    assert captured["timeout_seconds"] == 25.0
    assert captured["code"] == execution.wrap_for_sandbox(_SAMPLE_SOURCE)
    assert 'os.chdir("/tmp")' in captured["code"]


def test_unavailable_imports_flags_only_non_sandbox_modules():
    """stdlib + the allowlist are available; anything else is unavailable, in order (D61)."""
    assert execution.unavailable_imports(["re", "math", "json"]) == []        # stdlib
    assert execution.unavailable_imports(["numpy", "pandas", "yaml"]) == []   # allowlisted
    assert execution.unavailable_imports(["tensorflow", "torch"]) == ["tensorflow", "torch"]
    assert execution.unavailable_imports(["re", "scipy", "numpy"]) == ["scipy"]