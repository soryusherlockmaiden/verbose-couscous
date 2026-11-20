FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS base

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY .python-version pyproject.toml uv.lock ./
COPY src/ ./src/
COPY config/ ./config/

# Lint, type check and tests in a container: `docker compose run --rm check`.
FROM base AS check
COPY tests/ ./tests/
COPY evidence/m1/fetch.py ./evidence/m1/fetch.py
COPY evidence/m2/ingest_notices.py ./evidence/m2/ingest_notices.py
COPY evidence/m2/extract_exchangeinfo.py ./evidence/m2/extract_exchangeinfo.py
COPY evidence/m2/extract_trading_rules.py ./evidence/m2/extract_trading_rules.py
COPY evidence/m2/collect.py ./evidence/m2/collect.py
COPY evidence/m2/finalize.py ./evidence/m2/finalize.py
COPY evidence/m2/stage_b.py ./evidence/m2/stage_b.py
RUN uv sync --locked --no-editable
CMD ["sh", "-c", "/app/.venv/bin/ruff check . && /app/.venv/bin/mypy src evidence/m2 && /app/.venv/bin/pytest"]

# Runtime image (default target): no dev tools and no tests.
FROM base AS runtime
RUN uv sync --locked --no-dev --no-editable && \
    mkdir /data && chown 10001:10001 /data

USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python", "-m", "acgs", "--config", "/app/config/m0.toml", "--db", "/data/acgs.sqlite3"]
CMD ["smoke"]
