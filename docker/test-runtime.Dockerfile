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