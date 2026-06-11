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
    CMD python -c "import urllib.request, sys; sys.exit(0 if
urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

CMD ["uvicorn", "typewright.main:app", "--host", "0.0.0.0", "--port", "8000"]
# Put the venv's executables (uvicorn, python) first on PATH.
ENV PATH="/app/.venv/bin:$PATH"

# Run as an unprivileged user, not root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Liveness check that reuses the /health route, using only the stdlib (the slim
# base has no curl).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if
urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

CMD ["uvicorn", "typewright.main:app", "--host", "0.0.0.0", "--port", "8000"]