FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM python:3.12.13-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY .python-version pyproject.toml uv.lock ./
COPY src/ ./src/
COPY config/m0.toml ./config/m0.toml
RUN uv sync --locked --no-dev --no-editable && \
    mkdir /data && chown 10001:10001 /data

USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python", "-m", "acgs", "--config", "/app/config/m0.toml", "--db", "/data/acgs.sqlite3"]
CMD ["smoke"]
