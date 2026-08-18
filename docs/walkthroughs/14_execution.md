# 14 — `src/typewright/execution.py`

## What this file is for

This file is the **adapter that gets a test file ready for the sandbox, then sends it off**.

Phase 4 (unit 12) gave us a beautiful, clean pytest file: imports, the function under test, and a
set of `@given` property tests. It's written to be *portable* — you could save it as
`test_thing.py` on your own machine and run `pytest` on it, no fuss.

But the sandbox is a stranger, pickier place than your laptop. Inside Kestrel the folder you land
in is **read-only**, there's no command line running pytest for you, and a slow shared CPU can make
fast tests look like they hung. So before we can run that clean file in the sandbox, we have to add
a few lines that handle those quirks. That's what `execution.py` does: it **wraps** the clean file
in a small sandbox-specific jacket, then hands it to `kestrel.py` (unit 13) to run.

Think of it like prepping a houseplant to travel on a plane. The plant (the test file) is fine as
it is at home. But to fly, you wrap the pot so soil doesn't spill, label it, and put it in an
approved container. You don't change the plant — you add packaging suited to the journey. This file
is the packing step.

---

## A mental model: keep the clean file clean; add the sandbox jacket here

The one important idea is a **decision about where the sandbox-specific code lives** (decision
**D38**).

We had two choices for the extra "sandbox jacket" lines:

1. Bake them into the Phase 4 file itself, so the file we return is already sandbox-ready.
2. Keep the Phase 4 file clean and portable, and add the jacket **only at the moment we run it**.

We chose **option 2** — and `execution.py` is where the jacket gets added. Why? Because the Phase 4
file is something a human might want to read or run locally, and one of the jacket lines is
`os.chdir("/tmp")` — "switch into the `/tmp` folder." That makes perfect sense inside the sandbox
(it's the only writable spot there) but is nonsense, even harmful, on a normal machine. Baking it
in would make the returned file sandbox-only and a bit broken everywhere else. So the clean file
stays clean, and the sandbox knowledge stays here, next to the code that talks to the sandbox.

That's the whole philosophy: **the test file is portable; the sandbox quirks are packaging.**

---

## The whole file

```python
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

from .config import Settings, get_settings
from .kestrel import SandboxResult, run_in_sandbox
from .models import GeneratedTestFile

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
```

---

## Step-by-step

### `_PREAMBLE` — the lines we glue on top

```python
_PREAMBLE = (
    "import os, sys\n"
    'os.chdir("/tmp")  # only writable path in the sandbox\n'
    "from hypothesis import settings as _tw_settings\n"
    '_tw_settings.register_profile("sandbox", database=None, deadline=None)\n'
    '_tw_settings.load_profile("sandbox")\n'
)
```

Three small fixes for three sandbox quirks:

- **`os.chdir("/tmp")`** — *"work out of the `/tmp` folder."* Inside the sandbox, the folder your
  file lives in is **read-only** — you can't create files there. But lots of tools quietly try to:
  Python writes cache files, pytest writes a cache, Hypothesis writes a database of past examples.
  `/tmp` is the **one** writable folder in the sandbox, so we move into it first and every one of
  those background writes lands somewhere allowed.
- **`register_profile("sandbox", database=None, deadline=None)`** then **`load_profile("sandbox")`**
  — these tune Hypothesis (the property-testing engine) for the sandbox. `database=None` tells it
  *don't try to save a database of examples* (it has nowhere safe to save it). `deadline=None` tells
  it *don't fail a test just because one example was slow* — the sandbox shares a single slow CPU,
  so Hypothesis's normal "this example took too long" rule would fire constantly and report **fake**
  bugs. Turning the deadline off means the only failures we see are *real* ones.

### `_RUNNER` — the lines we glue on the bottom

```python
_RUNNER = (
    'if __name__ == "__main__":\n'
    "    import pytest\n"
    '    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))\n'
)
```

Here's a quirk that surprises people: Kestrel runs your file with `python main.py`. It does **not**
run `pytest` for you. But our file *is* a pytest file — left alone, `python` would just define some
test functions and exit without running any of them.

So we add a little launcher at the bottom: "if this file is being run directly, start pytest on
myself." `pytest.main([__file__, ...])` runs the tests in this very file; `sys.exit(...)` makes the
program's exit code equal pytest's result code (0 passed, 1 failed, …) — which is exactly the number
`kestrel.py` reads back to decide whether we found a bug. The `-q` keeps the output short, and
`-p no:cacheprovider` switches off pytest's cache-writing (another thing that would try to write to
the read-only folder).

### `wrap_for_sandbox(source)` — assemble the jacketed file

```python
def wrap_for_sandbox(source: str) -> str:
    return f"{_PREAMBLE}\n{source.strip()}\n\n\n{_RUNNER}"
```

This is just a sandwich: preamble on top, the clean Phase 4 file in the middle, runner on the
bottom, with blank lines between so it reads nicely and parses correctly. The `source` is dropped in
**untouched** — we add around it, never edit it. That's the whole point of D38: the original file is
preserved exactly; we only wrap.

### `run_tests(test_file, ...)` — wrap, then send

```python
def run_tests(test_file, *, timeout_seconds, settings=None) -> SandboxResult:
    settings = settings or get_settings()
    code = wrap_for_sandbox(test_file.source)
    return run_in_sandbox(code, timeout_seconds=timeout_seconds, settings=settings)
```

The top-level job, two steps: take the Phase 4 `GeneratedTestFile`, wrap its `source` into a
sandbox-ready string, and hand that to `run_in_sandbox` (unit 13) along with the time budget. It
returns the raw `SandboxResult` straight through — **this unit does not yet try to figure out
which tests failed.** Reading the result and pulling out the bugs is the next unit's job; this one
stops at "ran it, here's the raw report."

---

## What could go wrong

### 1. Baking the sandbox lines into the Phase 4 file
If we'd put `os.chdir("/tmp")` into the file Phase 4 returns, that file would break the moment a
developer tried to run it on their own machine (there may be no `/tmp`, or it's the wrong place to
be). Keeping the jacket here (D38) means the returned test file stays clean and portable, and only
the copy we actually ship to the sandbox carries the sandbox-specific lines.

### 2. Forgetting the `__main__` runner
Without the launcher at the bottom, Kestrel's `python main.py` would define the tests and then
exit having run **nothing** — and we'd happily report "exit code 0, all good!" when in truth nothing
was tested. The runner is what turns the file from "a definition of tests" into "a thing that
actually runs them."

### 3. Leaving Hypothesis's deadline on
The sandbox is CPU-starved. With the normal per-example deadline in force, Hypothesis would flag
slow-but-correct examples as failures, and we'd report bugs that aren't real. `deadline=None` is a
small setting that prevents a whole class of false alarms — which matters a lot for a tool whose
whole value is *trustworthy* bug reports.

### 4. Writing to the read-only folder
Any tool that tries to create a file next to the test file will error or warn inside the sandbox.
The combination of `os.chdir("/tmp")`, `-p no:cacheprovider`, and `database=None` covers the three
usual culprits (Python's, pytest's, and Hypothesis's caches). Miss one and the run gets noisy or
fails for reasons that have nothing to do with the code under test.

---

## Summary

`execution.py` is the packing-and-shipping step for Phase 5. `wrap_for_sandbox` takes the clean,
portable pytest file from Phase 4 and wraps it — a preamble on top (`os.chdir("/tmp")` so writes
land somewhere allowed; a Hypothesis profile with `database=None` and `deadline=None` so it neither
saves a database nor fails on slow examples) and a launcher on the bottom (so the file runs pytest
on itself, since Kestrel only does `python main.py`). `run_tests` wraps the file and hands it to
`kestrel.run_in_sandbox` with a time budget, returning the raw `SandboxResult`. The guiding decision
(D38) is to keep the original file untouched and add all sandbox-specific lines *here*, at run time,
so the test file stays clean and runnable anywhere. Interpreting the result into actual bugs is the
next unit's job.

---

## Change history

- **2026-06-19** — Created in Phase 5, Unit 1. Holds `wrap_for_sandbox()` (prepends the
  `/tmp` + Hypothesis-`database=None`/`deadline=None` preamble, appends the `__main__` pytest
  runner with `-p no:cacheprovider`) and `run_tests()` (wraps a `GeneratedTestFile`, submits via
  `kestrel.run_in_sandbox`, returns the raw `SandboxResult`). The preamble is added here, not baked
  into the Phase 4 output (D38), so `test_file.source` stays portable. Suite green at 57 passed.
- **2026-06-30** — Phase 10 (D61): added the sandbox dependency model — `SANDBOX_ALLOWLIST_IMPORTS` (the
  third-party packages baked into the `:0.2` runtime image, by import name: numpy/pandas/requests/dateutil/yaml/
  more_itertools + their transitive deps) and `unavailable_imports(imported_modules)`, which returns the imports
  that are neither stdlib (`sys.stdlib_module_names`) nor allowlisted. The route uses it to skip the sandbox +
  report honestly. **Keep the allowlist in sync with the Dockerfile (walkthrough 16).**
