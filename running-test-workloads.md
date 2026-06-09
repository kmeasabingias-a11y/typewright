# Running test workloads on Kestrel

Kestrel is a general-purpose sandbox: it accepts Python source over HTTP, runs it
in an isolated container, and returns captured stdout/stderr plus exit metadata. A
common use is running a **self-contained test file** — for example a generated
`pytest` + [Hypothesis](https://hypothesis.readthedocs.io/) property-test suite —
and reading back which tests failed and on what inputs.

This note explains how to configure a Kestrel deployment for that use and what a
caller's submitted file must do to run cleanly inside the sandbox. It is
caller-agnostic: anything that needs to execute test code in isolation (a
test-generation service, a CI shim, an autograder) uses Kestrel the same way.

## Execution model (what you're working with)

Stateless `POST /execute` runs your code as `python /sandbox/main.py` inside a
fresh, locked-down container. Two consequences shape everything below:

1. **You drive the test runner from inside the file.** Kestrel runs `python
   main.py` — it does not run `pytest` as the entrypoint and has no "command"
   parameter. So the file you submit must invoke pytest itself, typically:

   ```python
   if __name__ == "__main__":
       import sys, pytest
       sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
   ```

   The process exit code becomes pytest's exit code; pytest's report and any
   Hypothesis counter-examples land in stdout/stderr.

2. **Kestrel returns raw output, not parsed results.** The response is
   `{stdout, stderr, exit_code, duration_ms, timed_out, stdout_truncated,
   stderr_truncated}`. Turning that into "these tests failed on these inputs" is
   the caller's job — Kestrel stays test-framework-agnostic on purpose.

## 1. Server configuration (environment variables)

Set these on the Kestrel **service** (not per request). The defaults shown are
Kestrel's out-of-the-box values, which are tuned for quick snippets, not test
suites.

| Variable | Default | Set it because |
|---|---|---|
| `KESTREL_EXECUTOR_DOCKER_IMAGE` | `kestrel-runtime:0.5.0` | **Required.** The default image does **not** contain pytest or Hypothesis. Point this at a custom image that does (see §2). |
| `KESTREL_EXECUTE_TIMEOUT_SECONDS` | `5.0` | A Hypothesis run with thousands of examples will blow past 5 s. Raise to a realistic ceiling (e.g. `30`–`60`). This is also the **hard ceiling** for any per-request `timeout_seconds` (see §3). |
| `KESTREL_EXECUTE_OUTPUT_CAP_BYTES` | `65536` (64 KiB) | Verbose pytest output (many failures, `-v`) can exceed 64 KiB and be truncated. Raise if you need the full report. `stdout_truncated` / `stderr_truncated` in the response flag truncation. |
| `KESTREL_RATE_LIMIT_EXECUTE_PER_MINUTE` | `60` | A fleet submitting many runs under one API key will hit the 60/min token bucket. Raise to match throughput. |
| `KESTREL_DEV_API_KEY` *(or the Postgres key store)* | `""` (auth off) | Turn auth on for any non-local deployment and mint a key for your caller. |

Minimal env block for a test-workload Kestrel:

```bash
KESTREL_EXECUTOR_DOCKER_IMAGE=my-pytest-runtime:1.0
KESTREL_EXECUTE_TIMEOUT_SECONDS=30
KESTREL_EXECUTE_OUTPUT_CAP_BYTES=262144      # 256 KiB
KESTREL_RATE_LIMIT_EXECUTE_PER_MINUTE=600
KESTREL_DEV_API_KEY=change-me                # or use the Postgres-backed key store
```

### Fixed sandbox limits (not env-tunable)

Some isolation limits are hardcoded and apply to every `/execute` run. They matter
for test workloads because property testing can be resource-heavy:

- **Memory: 256 MiB** (`--memory 256m`; enforced on real Linux, not enforced under
  WSL2). Hypothesis generating large inputs can OOM here.
- **Writable scratch: `/tmp` only, a 64 MiB tmpfs.** The root filesystem is
  read-only (see §4).
- **Process limit: 64 pids.** pytest/Hypothesis spawning many workers or threads
  can hit this.
- **CPU: 1.0**, and **no network** (`--network none`).

The no-network rule means **every dependency the tests import must be baked into
the image** — there is no `pip install` at runtime, and tests that reach the
network won't work.

## 2. The custom runtime image

Build an image containing pytest, Hypothesis, and whatever libraries the
code-under-test imports. It does **not** need Kestrel's session kernel — stateless
`/execute` just runs `python /sandbox/main.py`, so a plain Python image with the
test dependencies is enough.

```dockerfile
# my-pytest-runtime:1.0
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir pytest hypothesis numpy pandas
# add any other libraries the submitted code imports
```

Make it available to the **host Docker daemon** that Kestrel drives:

- Single host: `docker build -t my-pytest-runtime:1.0 .` on that host.
- Compose / docker-out-of-docker: the image must exist on the host daemon (Kestrel
  shells out to `docker run <image>` against the host socket), so build it there or
  push it to a registry the host can pull.

Then set `KESTREL_EXECUTOR_DOCKER_IMAGE=my-pytest-runtime:1.0`.

## 3. Per-request timeout (optional)

`POST /execute` accepts an optional `timeout_seconds` (and the Python SDK exposes
`execute(code, *, timeout_seconds=...)`). It is **clamped down** to
`KESTREL_EXECUTE_TIMEOUT_SECONDS` — a request can ask for a *shorter* budget but
never exceed the server ceiling. So:

- To give one run a tighter fail-fast budget: set `timeout_seconds` below the
  ceiling.
- To allow *longer* runs: raise `KESTREL_EXECUTE_TIMEOUT_SECONDS`. The per-request
  field cannot lift the ceiling — that stays an operator decision.

When a run exceeds its budget, Kestrel kills it and returns `timed_out: true`,
`exit_code: -1`, and **empty** stdout/stderr (no partial output). Map that to your
"exceeded time budget" path.

## 4. Read-only-cwd constraints the submitted file must handle

This is the part that trips up test runners. The sandbox mounts the root
filesystem read-only and gives you exactly one writable location: `/tmp` (a 64 MiB
tmpfs). The working directory is `/sandbox`, which is **read-only** — it's where
your code file is bind-mounted. pytest and Hypothesis both default to writing under
the current directory:

| Tool | Wants to write | Under read-only cwd |
|---|---|---|
| CPython | `__pycache__/` bytecode | silently skipped (usually harmless) |
| pytest | `.pytest_cache/` | warning, or error |
| Hypothesis | `.hypothesis/` example database | error / warning |

**The simplest fix: change to `/tmp` at the top of the file**, so every relative
write lands on the writable tmpfs:

```python
import os
os.chdir("/tmp")   # only /tmp is writable; caches + hypothesis DB now have a home
```

**Belt-and-suspenders** (recommended, since some setups write to absolute paths or
refuse outright):

- **pytest:** pass `-p no:cacheprovider` to disable the cache plugin entirely.
- **Hypothesis:** use a profile with `database=None` so it never tries to persist
  examples.
- **Bytecode:** `ENV PYTHONDONTWRITEBYTECODE=1` in the image, so it takes effect at
  interpreter start.

### A self-contained submitted-file template

```python
import os, sys

os.chdir("/tmp")                 # only writable path in the sandbox

from hypothesis import settings
settings.register_profile("sandbox", database=None)
settings.load_profile("sandbox")

# ---------- code under test ----------
def parse_version(v: str) -> tuple[int, int, int]:
    a, b, c = v.split(".")
    return int(a), int(b), int(c)

# ---------- property tests ----------
from hypothesis import given, strategies as st

@given(st.text())
def test_parse_version_never_crashes_uncaught(v):
    try:
        parse_version(v)
    except (ValueError, IndexError):
        pass  # expected for malformed input

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
```

Submit the whole thing as the `code` field. With `os.chdir("/tmp")` +
`-p no:cacheprovider` + `database=None`, it runs cleanly with no
read-only-filesystem noise.

## 5. Reading the result

Kestrel returns the raw outcome; you parse it:

- **`exit_code`** follows pytest conventions: `0` = all passed, `1` = at least one
  test failed (a bug was found), `2` = collection/interruption error, `5` = no
  tests collected.
- **`timed_out: true`** → exceeded the (possibly per-request) budget; the run was
  killed and stdout/stderr come back empty.
- **Hypothesis counter-examples** appear in stdout/stderr as
  `Falsifying example: test_foo(v='...')` — parse them to extract the exact failing
  inputs.
- **`stdout_truncated` / `stderr_truncated`** → output hit the byte cap; raise
  `KESTREL_EXECUTE_OUTPUT_CAP_BYTES` if you need the rest.

### Example call (Python SDK)

```python
from kestrel_client import KestrelClient

with KestrelClient("https://kestrel.internal", api_key="kestrel_...") as k:
    result = k.execute(combined_test_file, timeout_seconds=30)

if result.timed_out:
    ...  # exceeded budget
elif result.exit_code == 0:
    ...  # all properties held
else:
    ...  # parse result.stdout for failing tests + Falsifying examples
```

Or over plain HTTP:

```bash
curl -sS https://kestrel.internal/execute \
  -H "Authorization: Bearer kestrel_..." \
  -H "Content-Type: application/json" \
  -d '{"code": "<combined test file>", "timeout_seconds": 30}'
```

## Division of responsibility

Kestrel provides the sandbox and the raw result. The caller owns:

- building the runtime image (pytest/Hypothesis + the libraries the tests import),
- constructing the submitted file (driving pytest; the `/tmp` + cache-disable
  preamble),
- parsing pytest/Hypothesis output into structured pass/fail + counter-examples,
- choosing timeouts and limits for its workload.

That split is deliberate: it keeps Kestrel a general execution sandbox, usable by
any test-running (or non-test) caller, with nothing framework- or
product-specific baked in.
