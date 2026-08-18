# 08 — `Dockerfile`

## What this file is for

This file is TypeWright's **shipping crate**.

Up to now, running the service has meant *"on my machine, with my Python, after
`uv sync`."* That's fine for development, but it doesn't travel. The moment you
want to run TypeWright somewhere else — a teammate's laptop, a cloud server, a CI
runner — all those quiet assumptions ("the right Python is installed," "the right
packages are present," "the right command starts it") become someone else's
problem to rediscover.

A **Dockerfile** is a recipe for building a self-contained box — a **container
image** — that has *everything* the app needs inside it: a Python interpreter, the
exact dependency versions, the application code, and the command to start it. Hand
that box to any machine with Docker, and it runs the same way it ran for you.
Think of it like vacuum-sealing a meal with its own plate, cutlery, and reheating
instructions: the kitchen it lands in doesn't need to own any of that.

For TypeWright this is the Phase 1 deliverable that makes the service *portable*
and *reproducible* (DECISIONS.md D12): one command builds the image, one command
runs it, and what comes out is a working web service answering on a port.

---

## A mental model: images, layers, and the build

A few ideas make the file read easily.

**1. Image vs. container.** An **image** is the sealed box you build once — a frozen
snapshot. A **container** is a running copy of that box. You build an image, then
start as many containers from it as you like. The Dockerfile describes how to build the
image.

**2. A build is a stack of layers.** Each instruction in a Dockerfile (`COPY`, `RUN`,
…) adds a **layer** on top of the previous one. The magic is **caching**: if an
instruction and everything it depends on haven't changed since last build, Docker reuses
the cached layer instead of redoing the work. This is why the *order* of instructions
matters enormously — and it shapes the whole file below.

**3. The order trick: slow-and-stable first, fast-and-changing last.** Your dependency
list changes rarely; your source code changes constantly. So we install dependencies
*before* copying the source. That way, editing a `.py` file only rebuilds the cheap last
layers — the expensive dependency install stays cached. Put them the other way round and
every one-character code edit would re-download the whole dependency tree.

**4. We reuse the project's own tools.** TypeWright develops with **uv** (the fast Python
package manager) and a **lockfile** (`uv.lock`) that records the *exact* version of every
dependency. The image build uses those same two things, so what's installed in the box is
byte-for-byte what you tested with locally. No "works here, breaks there" version drift.

**5. `--frozen` means "no surprises."** Building with `uv sync --frozen` tells uv: install
*exactly* what the lockfile says and refuse to quietly change it. A reproducible build
should never invent new versions on its own.

---

## The whole file

```dockerfile
# syntax=docker/dockerfile:1
#
# TypeWright application image (DECISIONS.md D12: app Dockerfile now, compose
# deferred). Reproducible uvicorn run of the Phase 1 service. Dependencies are
# resolved from the committed uv.lock, the project is installed into a venv, and
# the container runs as a non-root user.

FROM python:3.12-slim AS runtime

# - PYTHONUNBUFFERED: log lines flush immediately (no lost output on crash).
# - PYTHONDONTWRITEBYTECODE: don't litter the image with .pyc files.
# - UV_*: install into a known venv, copy (not hardlink) across the cache mount,
#   compile bytecode at install time, and never download a Python — use the
#   interpreter the base image already provides.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=python3.12 \
    UV_PYTHON_DOWNLOADS=never

# The uv binary, pinned to the version this project develops against.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

WORKDIR /app

# 1) Install ONLY third-party dependencies first. This layer is cached and reused
#    on every build where pyproject.toml and uv.lock haven't changed — so editing
#    source code doesn't re-resolve the dependency tree. --no-dev keeps test-only
#    packages (pytest, hypothesis) out of the runtime image.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# 2) Now add the application source and install the project itself. README.md is
#    copied because pyproject.toml references it as the package readme, so the
#    wheel build needs it present.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Put the venv's executables (uvicorn, python) first on PATH.
ENV PATH="/app/.venv/bin:$PATH"

# Run as an unprivileged user, not root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Liveness check that reuses the /health route, using only the stdlib (the slim
# base has no curl).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

CMD ["uvicorn", "typewright.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Step-by-step

### The base image

```dockerfile
FROM python:3.12-slim AS runtime
```

`FROM` picks the starting box we build on top of. `python:3.12-slim` is an official,
trimmed-down image that already has Python 3.12 and very little else — small, which means
faster to download and a smaller attack surface. `AS runtime` just names this stage
(useful if the file later grows more stages; for now it's a clear label).

### The environment knobs

```dockerfile
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=python3.12 \
    UV_PYTHON_DOWNLOADS=never
```

`ENV` sets environment variables that persist into the running container. In plain terms:

- **`PYTHONUNBUFFERED=1`** — print log lines the instant they happen, instead of holding
  them in a buffer. If the process dies, you still see its last words. Crucial for a
  service whose logs you actually watch.
- **`PYTHONDONTWRITEBYTECODE=1`** — don't scatter `.pyc` cache files around; the image
  stays cleaner. (We compile bytecode deliberately once, below, instead.)
- **`UV_PROJECT_ENVIRONMENT=/app/.venv`** — tell uv to build the virtual environment at a
  fixed, known path, so the rest of the file can point at it.
- **`UV_LINK_MODE=copy`** — copy files into the venv rather than hard-linking them from
  the cache. Hard-links can't cross the cache "mount" we use below, so copying avoids a
  warning and is the correct mode here.
- **`UV_COMPILE_BYTECODE=1`** — pre-compile the installed code to bytecode *at build
  time*. That shifts a one-time cost from the first request (slightly faster startup) into
  the build.
- **`UV_PYTHON=python3.12` + `UV_PYTHON_DOWNLOADS=never`** — together these say "use the
  Python that's already in this base image; never go off and download another one." Without
  them, uv might try to fetch its own managed interpreter, bloating the image and the build.

### Bringing in uv

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
```

This is a neat trick. Instead of *installing* uv with a script, we **copy the `uv` binary
straight out of uv's own published image** into ours. `--from=<image>` means "the source
of this COPY is that other image, not our build context." We pin `0.11.7` — the exact
version this project develops with — so the build tool itself is reproducible, not
"whatever was latest today."

### The two-step install (the heart of the file)

```dockerfile
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
```

`WORKDIR /app` sets the working folder for everything after it. Then comes **step 1** —
and this is the order trick in action:

- We copy **only** `pyproject.toml` and `uv.lock` — the files that describe the
  dependencies — and nothing else yet.
- `uv sync` installs them. The flags matter:
  - `--frozen` — install exactly what the lockfile pins; don't re-resolve.
  - `--no-install-project` — install the *dependencies* but **not TypeWright itself** yet
    (we don't even have the source copied in at this point).
  - `--no-dev` — skip the dev-only group (pytest, hypothesis, httpx). They're for testing,
    not for the running service; leaving them out keeps the image lean.
- `--mount=type=cache,target=/root/.cache/uv` gives uv a **persistent download cache** that
  survives between builds, so re-builds don't re-download packages they already fetched.

Because this layer depends only on those two files, Docker **reuses it from cache** on
every build where the dependencies haven't changed — even if you edited the source a
hundred times. That's the payoff of copying them first.

```dockerfile
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
```

**Step 2** brings in the actual application and installs *it*. Now `uv sync` (without
`--no-install-project`) builds and installs the TypeWright package itself into the venv.
Two details worth knowing:

- We copy **`README.md`** as well as the source. Why? `pyproject.toml` lists the README as
  the package's description file, so building the package's wheel *requires* that file to
  exist. Forget it and the build fails with a confusing error. (This is exactly the kind
  of thing the verify-before-ship build catches.)
- This step re-runs only when `src/` or the README change — which is most of the time
  during development — while step 1 stays cached. Fast inner loop.

### Wiring up the runtime

```dockerfile
ENV PATH="/app/.venv/bin:$PATH"
```

Putting the venv's `bin` folder first on `PATH` means typing `uvicorn` or `python` finds
the versions *inside our virtual environment* — the ones we just installed — without
having to spell out the full path every time.

```dockerfile
RUN useradd --create-home --uid 10001 appuser
USER appuser
```

By default a container runs as **root**, the all-powerful admin user. That's needlessly
risky: if the app were ever compromised, the attacker would start with root inside the
container. So we create an ordinary, unprivileged user (`appuser`) and `USER appuser`
switches to it for everything that follows. The app has no need for root, so we don't give
it root. (Note the order: we install *as root*, because installing needs write access to
system paths, then drop to `appuser` for *running*.)

```dockerfile
EXPOSE 8000
```

`EXPOSE` is documentation-with-intent: it declares that the service listens on port 8000.
It doesn't open the port by itself — when you run the container you map it with
`-p <host>:8000` — but it records the contract for humans and tools.

### The health check

```dockerfile
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"
```

This teaches Docker how to ask the running container "are you actually healthy?" — not
just "is the process alive," but "does it answer correctly?" Every 30 seconds, Docker runs
the little Python one-liner, which calls our own `/health` endpoint (from unit 06) and
exits `0` (healthy) only if it gets a `200` back. The other knobs: wait `5s` after start
before checking (`--start-period`), give each check `3s` to respond (`--timeout`), and only
declare the container "unhealthy" after `3` consecutive failures (`--retries`).

Why a Python one-liner and not `curl`? Because the `slim` base image doesn't include
`curl`, but it *does* include Python (that's the whole point of the image) — so we reuse
the stdlib and add no extra packages.

### The start command

```dockerfile
CMD ["uvicorn", "typewright.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`CMD` is what runs when the container starts. It launches **uvicorn** (the web server) and
points it at `app` inside `typewright/main.py` — the very `app = create_app()` object from
unit 06. Two specifics:

- **`--host 0.0.0.0`** — listen on *all* network interfaces inside the container, not just
  `localhost`. This is required for the mapped port to be reachable from outside the
  container; the default `127.0.0.1` would only accept connections from within the box.
- **`--port 8000`** — match the port we `EXPOSE`d and health-check.

The `[...]` list form ("exec form") runs uvicorn directly as the main process, so signals
like "please stop" reach it cleanly — important for a container that needs to shut down
gracefully.

---

## Trying it out

```sh
# Build the image (run from the repo root, where the Dockerfile lives):
docker build -t typewright:latest .

# Run it, mapping container port 8000 to your machine's 8000:
docker run --rm -p 8000:8000 typewright:latest

# In another terminal — the same Phase 1 exit criteria, now from a container:
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"code": "def add(a: int, b: int) -> int:\n    return a + b"}'
```

You get back `{"status":"ok"}` and the analyzed-function JSON — exactly what the local
`uvicorn` run produced, but now from a sealed, portable box. `docker ps` will also show the
container's health flip to **healthy** a few seconds after start.

---

## What could go wrong

### 1. Copying source before dependencies (cache thrash)
The single most common Dockerfile mistake. If `COPY src ./src` came *before* the
dependency install, then every code edit — even a typo fix — would invalidate the cache and
re-run the slow dependency resolution. Copying `pyproject.toml` + `uv.lock` first, and
source last, keeps the expensive layer cached across the edits you make most often.

### 2. Shipping the test tools to production
Without `--no-dev`, the image would carry pytest, hypothesis, and httpx — packages the
running service never uses. That's wasted size and extra code to worry about. `--no-dev`
keeps the runtime image to just what serves requests.

### 3. Forgetting the README and getting a baffling build error
Because `pyproject.toml` names `README.md` as the package readme, the wheel build *needs*
that file. Skip the `COPY README.md` and the build fails deep inside packaging with a
message that doesn't obviously point at "you forgot a markdown file." Copying it alongside
the source avoids the trap.

### 4. Letting uv download its own Python
If `UV_PYTHON_DOWNLOADS` weren't set to `never`, uv might decide to fetch a managed
interpreter instead of using the one already baked into `python:3.12-slim` — needlessly
inflating both build time and image size. Pinning it to the base image's Python keeps the
box small and the interpreter predictable.

### 5. Running as root out of habit
The default container user is root. A service that never needs admin rights shouldn't run
with them — if it's ever exploited, root inside the container is a far worse starting point
for an attacker than an unprivileged `appuser`. Creating the user and `USER appuser` is a
cheap, standard hardening step.

### 6. A health check that lies
A naive health check that just confirms "the process exists" would call a hung,
not-actually-serving process "healthy." Hitting the real `/health` endpoint and requiring a
`200` makes the check mean what it says: the service is *answering correctly*, not merely
running.

### 7. Binding to localhost inside the container
If uvicorn bound to the default `127.0.0.1`, the mapped `-p` port would connect to nothing —
"localhost" inside the container isn't your machine. `--host 0.0.0.0` is what makes the
service reachable from outside the box. A classic "why won't it connect?" gotcha.

---

## Summary

The `Dockerfile` is TypeWright's shipping crate: it builds a sealed, reproducible image
with the exact Python and dependency versions the project was tested against, so the
service runs the same anywhere Docker does (DECISIONS.md D12). Its shape is driven by build
caching — dependencies (slow, stable) install before source (fast, changing) — and it reuses
the project's own `uv` + `uv.lock` for byte-for-byte reproducibility. It installs without
the dev/test tools (`--no-dev`), compiles bytecode for faster startup, drops from root to an
unprivileged `appuser`, declares port 8000, and adds a real `/health`-based health check
before starting the app with `uvicorn`. Built and run, it answers the same `/health` and
`/v1/analyze` calls as the local server — the Phase 1 service, now portable.

---

## Change history

- **2026-06-11** — Created in Phase 1, Unit 8. Single-stage `python:3.12-slim` image; uv
  binary pinned to `0.11.7` and copied from the official image; two-step `uv sync --frozen
  --no-dev` install (deps first for cache reuse, project second); `README.md` copied for the
  wheel build; bytecode compiled; non-root `appuser`; `EXPOSE 8000`; stdlib `/health`
  `HEALTHCHECK`; `uvicorn typewright.main:app` as `CMD` (D12). Verified: image builds, both
  endpoints respond from a running container, and the container reports **healthy**
  (image ≈ 314 MB). docker-compose deferred until a second container exists (D12).
