# ==========================================
# Stage 1: Build with Python 3.13 and uv
# ==========================================
FROM python:3.13-slim-bookworm AS builder

# Copy uv from the official uv image (multi-arch, signed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copy dependency manifests first to leverage Docker layer caching.
# Any change in pyproject.toml / uv.lock invalidates this layer.
COPY pyproject.toml uv.lock .python-version ./

# Install dependencies into .venv (no project, no dev deps) with BuildKit cache
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy the rest of the source code
COPY . .

# Install the project itself (still without dev deps)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ==========================================
# Stage 2: Production runtime image
# ==========================================
FROM python:3.13-slim-bookworm AS runtime

WORKDIR /app

# Create a non-privileged user for the runtime container
RUN groupadd -r edge && useradd -r -g edge edge

# Copy the entire project (including the .venv built in the builder stage)
# Note: --chown only applies to the copied *contents*; the destination /app was
# already created by WORKDIR as root, so we chown the whole tree explicitly.
COPY --from=builder /build /app
RUN chown -R edge:edge /app

# Activate the project virtualenv on PATH and silence .pyc writes / unbuffered stdout
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER edge:edge

# Edge service port: same default as a direct launch (override at run-time with `-e PORT=...`)
ENV PORT=5000
EXPOSE 5000

# Quick liveness probe against Flask /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; \
urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '5000'), timeout=3).read()" \
    || exit 1

ENTRYPOINT ["sh", "-c", "exec python app.py"]