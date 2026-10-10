# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------------------------
# Builder: install locked dependencies into a virtualenv with uv
# ---------------------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------------------------
# Test: same dependencies + dev tools (pytest, testcontainers, coverage, ruff) + AI evaluation (DeepEval).
#   docker compose --profile test run --rm backend-tests
# ---------------------------------------------------------------------------------------------
FROM builder AS test
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --group eval
COPY app ./app
COPY tests ./tests
ENV PATH="/opt/venv/bin:$PATH" APP_ENV=test PYTHONDONTWRITEBYTECODE=1
CMD ["sh", "-c", "ruff check app tests && pytest -q"]

# ---------------------------------------------------------------------------------------------
# Runtime: minimal image, non-root user, no build tools
# ---------------------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="novatech-hiring-backend" \
      org.opencontainers.image.description="NovaTech hiring automation backend API"

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data/storage \
 && chown -R app:app /data

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app /app/app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STORAGE_DIR=/data/storage

WORKDIR /app
USER app
EXPOSE 8000
VOLUME ["/data/storage"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header", "--timeout-graceful-shutdown", "20", \
     "--timeout-worker-healthcheck", "30"]
# The worker health check defaults to 5 s: on a busy machine a worker in the middle of an AI call (or still
# starting) missed it, was killed and restarted in a loop, failing every request meanwhile.
