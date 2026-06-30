"""Phase 5: run a generated test file in the Kestrel sandbox.

``run_tests`` takes a Phase 4 ``GeneratedTestFile``, wraps its ``source`` with the
preamble Kestrel's read-only-cwd execution model requires (running-test-workloads.md
§4), submits it through ``kestrel.run_in_sandbox``, and returns the raw
``SandboxResult``. Turning that raw result into structured bugs is the next unit's job.

The preamble is added HERE, not baked into the Phase 4 output (D38): ``test_file.source``
stays a clean, portable pytest file a developer can run locally, while the sandbox-only
bits live with the sandbox layer that needs them:

* ``os.chdir("/tmp")`` — the sandbox mounts cwd read-only; /tmp is the one writable path,
so caches and the Hypothesis DB have somewhere to go.
* a database-less, deadline-less Hypothesis profile — it must not try to persist its
example DB under the read-only cwd, and the constrained 1-CPU sandbox's slowness must
not trip Hypothesis's per-example deadline into a false "failure".
* a ``__main__`` pytest runner — Kestrel runs ``python main.py``, not pytest, so the file
drives pytest itself; its process exit code becomes pytest's (0 pass / 1 failures /
2 collection error / 5 none collected).
"""

from __future__ import annotations

import sys

from .config import Settings, get_settings
from .kestrel import SandboxResult, run_in_sandbox
from .models import GeneratedTestFile

# Third-party packages baked into the sandbox runtime image (docker/test-runtime.Dockerfile, D61),
# as the IMPORT names a function would use. The network-less sandbox can't install anything, so
# this allowlist is the only third-party that runs; anything else is reported as unavailable, not
# as a phantom crash. Transitive deps (six, pytz, certifi, …) are listed so importing them directly
# isn't false-flagged. MUST stay in sync with the pip installs in that Dockerfile.
SANDBOX_ALLOWLIST_IMPORTS = frozenset({
    "numpy", "pandas", "requests", "dateutil", "yaml", "more_itertools",
    "six", "pytz", "tzdata", "certifi", "urllib3", "idna", "charset_normalizer",
})

# Everything importable in the sandbox: the full stdlib + the two test tools + the allowlist.
_SANDBOX_AVAILABLE = frozenset(sys.stdlib_module_names) | {"pytest", "hypothesis"} | SANDBOX_ALLOWLIST_IMPORTS


def unavailable_imports(imported_modules: list[str]) -> list[str]:
    """The function's imports the sandbox can't provide (not stdlib, not in the allowlist; D61)."""
    return [m for m in imported_modules if m not in _SANDBOX_AVAILABLE]

# Prepended so every relative write lands on the writable tmpfs and Hypothesis neither
# persists examples nor enforces a deadline under the constrained sandbox.
_PREAMBLE = (
    "import os, sys\n"
    'os.chdir("/tmp")  # only writable path in the sandbox\n'
    "from hypothesis import settings as _tw_settings\n"
    '_tw_settings.register_profile("sandbox", database=None, deadline=None)\n'
    '_tw_settings.load_profile("sandbox")\n'
)

# Appended: Kestrel runs ``python main.py`` (no pytest entrypoint), so the file invokes
# pytest itself; ``-p no:cacheprovider`` keeps pytest from writing its cache to cwd.
_RUNNER = (
    'if __name__ == "__main__":\n'
    "    import pytest\n"
    '    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))\n'
)


def wrap_for_sandbox(source: str) -> str:
    """Wrap a standalone pytest file with the sandbox preamble + pytest runner."""
    return f"{_PREAMBLE}\n{source.strip()}\n\n\n{_RUNNER}"


def run_tests(
    test_file: GeneratedTestFile,
    *,
    timeout_seconds: float,
    settings: Settings | None = None,
) -> SandboxResult:
    """Wrap the generated test file for the sandbox and execute it in Kestrel."""
    settings = settings or get_settings()
    code = wrap_for_sandbox(test_file.source)
    return run_in_sandbox(code, timeout_seconds=timeout_seconds, settings=settings)