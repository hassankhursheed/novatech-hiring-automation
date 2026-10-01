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

# Optional provider packages, e.g. --build-arg LLM_EXTRAS="--extra mistral --extra google"
ARG LLM_EXTRAS=""

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev ${LLM_EXTRAS}

# ---------------------------------------------------------------------------------------------
# Test: same dependencies + dev tools. Runs lint, type-independent unit/API tests and (when
# DATABASE_URL_TEST is set) database integration tests:  docker compose --profile test run --rm backend-tests
# ---------------------------------------------------------------------------------------------
FROM builder AS test
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen ${LLM_EXTRAS}
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
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header", "--timeout-graceful-shutdown", "20"]
