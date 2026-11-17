FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM python:3.12.13-slim-bookworm AS base

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY .python-version pyproject.toml uv.lock ./
COPY src/ ./src/
COPY config/m0.toml ./config/m0.toml

# Lint, type check and tests in a container: `docker compose run --rm check`.
FROM base AS check
COPY tests/ ./tests/
RUN uv sync --locked --no-editable
CMD ["sh", "-c", "/app/.venv/bin/ruff check . && /app/.venv/bin/mypy && /app/.venv/bin/pytest"]

# Runtime image (default target): no dev tools and no tests.
FROM base AS runtime
RUN uv sync --locked --no-dev --no-editable && \
    mkdir /data && chown 10001:10001 /data

USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python", "-m", "acgs", "--config", "/app/config/m0.toml", "--db", "/data/acgs.sqlite3"]
CMD ["smoke"]
