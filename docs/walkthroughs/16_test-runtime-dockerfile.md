# 16 — `docker/test-runtime.Dockerfile`

## What this file is for

When TypeWright finishes writing a property-test file, it doesn't run that file on
its own machine — running code from the internet on your own server is dangerous.
Instead it hands the file to **Kestrel**, a separate "sandbox" service whose whole
job is to run untrusted code safely, inside a throwaway, locked-down container.

But Kestrel needs to know *what kind of container* to run the code in. An empty
Python container can't run our tests — our tests `import pytest` and `import
hypothesis`, and those aren't part of plain Python. So somebody has to build a
container image that has those tools pre-installed. **This file is the recipe for
that image.**

A useful analogy: Kestrel is a sealed, fireproof test booth. Our test file is the
experiment we want to run inside it. This Dockerfile is the **toolkit we bolt to
the booth's wall** before the experiment goes in — it makes sure pytest and
Hypothesis are already on the shelf, because once the booth is sealed there's no
network to go fetch them.

This is the *one* place those two test tools live for the sandbox. Note it's a
**different** image from the `Dockerfile` at the repo root (walkthrough 08): that
one packages the TypeWright *service itself* (the API). This one packages the tiny
environment the generated *tests* run in. Two images, two jobs.

## A mental model

Three ideas make everything below obvious:

1. **No network inside the sandbox.** Kestrel runs the container with
   `--network none`. So you can't `pip install` anything at run time — every
   library the tests touch has to be **baked into the image at build time**. That's
   the entire reason this file exists.

2. **Kestrel is in charge of *how* the container runs, not us.** Kestrel launches it
   with a fixed command:
   ```
   docker run --user 65534:65534 --read-only --tmpfs /tmp:size=64m \
              --workdir /sandbox  <our-image>  python /sandbox/main.py
   ```
   It picks the user, the working directory, *and* the command. So anything we'd
   normally put in a Dockerfile to control those (`USER`, `WORKDIR`, `CMD`) would
   just be ignored. We leave them out — the image only needs to provide the
   *interpreter and the libraries*, and let Kestrel drive.

3. **The "run cleanly in a read-only box" preamble is added elsewhere.** You might
   expect this image to handle the awkward bits — the filesystem is read-only except
   `/tmp`, so pytest and Hypothesis have nowhere to write their caches. But that's
   solved in **`execution.py`** (walkthrough 14), which prepends `os.chdir("/tmp")`,
   a database-less Hypothesis profile, and a `__main__` runner to every test file
   *at the moment it's submitted* (decision D38). So this image stays dumb on
   purpose: just Python + the two test tools.

## The whole file

```dockerfile
# syntax=docker/dockerfile:1
#
# TypeWright test-runtime image (DECISIONS.md D43).
#
# The image Kestrel runs each generated property-test file inside. /v1/analyze
# submits a self-contained pytest+Hypothesis file to Kestrel's stateless
# POST /execute; Kestrel runs it as `python /sandbox/main.py` in a locked-down,
# NETWORK-LESS container — so every dependency the tests import must be baked in
# here (running-test-workloads.md §2). The submitted file imports only hypothesis
# + pytest (plus stdlib the function-under-test uses); execution.py adds the
# os.chdir("/tmp") / DB-less profile / __main__ runner preamble at run time (D38),
# so this image stays a plain interpreter + the two test deps.
#
# Kestrel drives the container with `docker run --user 65534:65534 --read-only
# --workdir /sandbox <image> python /sandbox/main.py`, OVERRIDING any USER, WORKDIR,
# and CMD — so we set none of those (they'd be ignored). PYTHONDONTWRITEBYTECODE is
# the one runtime behavior we MUST bake in as ENV: Kestrel doesn't pass it, and the
# read-only rootfs would otherwise make CPython's .pyc writes noisy (§4).
#
# Python 3.12 matches TypeWright's dev + app image, so tests run on the same
# interpreter they were generated against. pytest/hypothesis are pinned to the exact
# uv.lock versions so the sandbox emits the same "FAILED …" / "Falsifying example:"
# markers results.py text-scrapes.
#
# Build (on the SAME Docker daemon Kestrel drives — for local WSL2, the host daemon):
#   docker build -f docker/test-runtime.Dockerfile -t typewright-test-runtime:0.1 .
# Then point Kestrel at it:  KESTREL_EXECUTOR_DOCKER_IMAGE=typewright-test-runtime:0.1

FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: no __pycache__ writes under the read-only sandbox rootfs.
# PYTHONUNBUFFERED: flush stdout/stderr immediately so Kestrel captures full output
# even when a run is killed at the timeout.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Exact uv.lock versions — parity with the env the tests are generated against keeps
# Hypothesis/pytest output formatting stable for results.py's text-scraping parser.
RUN pip install --no-cache-dir \
        pytest==9.0.3 \
        hypothesis==6.155.2

# Fail the build early if the install is broken — proves `import pytest, hypothesis`
# works. pip's system install is world-readable, so uid 65534 can import it at run time.
RUN python -c "import pytest, hypothesis; print(pytest.__version__, hypothesis.__version__)"
```

## Step-by-step

**`FROM python:3.12-slim`** — start from the official slim Python image. *Why 3.12
specifically?* Because TypeWright's own code, its app image, and the test files it
generates are all built and parsed against Python 3.12. If the sandbox ran the tests
on, say, 3.11, you could get a failure (or a false pass) caused purely by the version
difference rather than by a real bug in the function. Matching versions removes that
whole class of confusion (decision D43). `slim` keeps the image small; we don't need
the build toolchain a full image carries.

**`ENV PYTHONDONTWRITEBYTECODE=1`** — tells Python not to write `.pyc` cache files
next to the code it imports. Inside the sandbox the filesystem is read-only
everywhere except `/tmp`, so a `.pyc` write would either fail or print noise. Setting
it as an *image environment variable* is the only way to get it in place — Kestrel
runs `python /sandbox/main.py` without passing this flag, so we can't add it at run
time. (`PYTHONUNBUFFERED=1` is a companion: it makes Python flush its output
immediately, so if Kestrel kills a slow run at the timeout we still see whatever the
tests printed up to that point.)

**`RUN pip install … pytest==9.0.3 hypothesis==6.155.2`** — the heart of the image.
These are the two tools every generated test file needs, and they're **pinned to
exact versions** — the same versions recorded in TypeWright's `uv.lock`. The pinning
isn't fussiness: `results.py` (walkthrough 15) reads bugs back out by *scanning the
text* pytest and Hypothesis print — lines like `FAILED main.py::test_x - …` and
`Falsifying example: test_x(s='   ')`. If a newer release of either tool reworded
those lines, the parser would quietly stop finding bugs. Pinning guarantees the
sandbox speaks the exact dialect the parser expects. `--no-cache-dir` keeps pip's
download cache out of the final image.

**`RUN python -c "import pytest, hypothesis; …"`** — a build-time self-test. If the
install above somehow produced a broken environment, this line fails and the *build*
fails, loudly, right now — instead of every test run later mysteriously erroring with
"no module named pytest." It also prints the two version numbers so the build log
shows exactly what got baked in (you should see `9.0.3 6.155.2`).

**What's deliberately absent:** no `USER`, no `WORKDIR`, no `CMD`/`ENTRYPOINT`. A
normal app image sets all three. Here they'd be dead weight, because Kestrel
overrides every one of them when it launches the container (it forces user
`65534:65534`, working directory `/sandbox`, and the command `python
/sandbox/main.py`). Leaving them out isn't laziness — it's the image honestly saying
"I only provide the interpreter and the libraries; the sandbox decides how I'm run."

## What could go wrong

- **"No module named pytest" at run time.** The image wasn't built, or Kestrel is
  pointed at the wrong image. Kestrel chooses the image via the
  `KESTREL_EXECUTOR_DOCKER_IMAGE` environment variable — it must equal the tag you
  built (`typewright-test-runtime:0.1`), and the image must exist **on the same
  Docker daemon Kestrel drives**. Locally on WSL2 that's the host daemon, which is
  where `docker build` puts it — fine. But if you run Kestrel itself in a container
  (`docker compose up`), it shells out to the *host* daemon, so the image still has
  to be on the host, and Kestrel additionally needs `KESTREL_EXEC_SPOOL_DIR` set so
  the file it mounts in resolves. Running Kestrel's API directly with `uvicorn`
  avoids that wrinkle.

- **Forgetting to rebuild after a dependency bump.** If TypeWright's `uv.lock` later
  moves to a newer pytest or Hypothesis, this image must be rebuilt (and re-tagged,
  e.g. `:0.2`) to keep parity. Stale image = the sandbox runs different versions than
  the tests were written for, and the text-scraping parser can drift. The version
  numbers are duplicated here and in `uv.lock` on purpose — treat a lock bump as a
  reminder to rebuild.

- **Trying to add `pip install` inside the test file.** Won't work: the sandbox has
  `--network none`. If a submitted function imports a third-party library (say
  `numpy`), the test will fail with an import error until that library is **added to
  this image** and the image rebuilt. Today every generated file only needs the
  standard library plus pytest/Hypothesis, so the image stays minimal — but this is
  the file you extend when that changes.

- **Caches or the Hypothesis database failing to write.** That's the read-only
  filesystem biting. It's *not* fixed here — it's fixed by the preamble
  `execution.py` adds (`os.chdir("/tmp")` + a `database=None` Hypothesis profile,
  D38). If you ever see those errors, the bug is in the preamble, not this image.

- **Expecting the 256 MiB memory cap to catch a runaway test locally.** Kestrel asks
  for `--memory 256m`, but that limit **isn't enforced under WSL2**. So a memory-hungry
  test that would be killed on a real Linux host may run to completion locally. Don't
  rely on a local smoke to prove memory safety.

## Change history

- **2026-06-23 — created (Phase 5, runtime image, D43).** Added
  `docker/test-runtime.Dockerfile`: `python:3.12-slim` + pinned
  `pytest==9.0.3`/`hypothesis==6.155.2`, `PYTHONDONTWRITEBYTECODE`/`PYTHONUNBUFFERED`
  env, and a build-time import self-test. No `USER`/`WORKDIR`/`CMD` because Kestrel's
  executor overrides all three. This completes Phase 5's deliverables; the live
  end-to-end smoke against a running Kestrel is the final verification step.
- **2026-06-30 — bumped to `:0.2` (Phase 10, D61).** Added a curated **third-party allowlist** —
  `numpy pandas requests python-dateutil PyYAML more-itertools` (all manylinux wheels, no compiler/system
  libs) — so common pasted dependencies actually run in the sandbox; image ~201MB → ~402MB. The set is mirrored
  by import name in `execution.SANDBOX_ALLOWLIST_IMPORTS` — **keep the two in sync.** The build self-test now
  imports the allowlist too. "Make all third-party run" was rejected (can't bake all of PyPI; the sandbox is
  network-less). Verified: numpy/pandas/`re` run under Kestrel's exact `--read-only --user 65534 --network none`
  flags, and a live `/v1/analyze` numpy round-trip ran clean. Deploy refs (compose/DEPLOY/README) bumped
  `:0.1`→`:0.2`; the D43 build commands above still say `:0.1` as the historical record of the original image.
