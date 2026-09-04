FROM ghcr.io/astral-sh/uv@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 AS uv

FROM python@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-build --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src

FROM python@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY --from=builder /app /app

RUN rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip \
        /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.13/site-packages/setuptools \
        /usr/local/lib/python3.13/site-packages/setuptools-*.dist-info \
        /usr/local/lib/python3.13/site-packages/pkg_resources \
    && groupadd --system --gid 10001 mistraldock \
    && useradd --system --uid 10001 --gid mistraldock --home /nonexistent --shell /usr/sbin/nologin mistraldock \
    && mkdir /data \
    && chown mistraldock:mistraldock /data

USER mistraldock

EXPOSE 8080

CMD ["sh", "-c", ".venv/bin/alembic upgrade head && .venv/bin/uvicorn mistraldock.api:create_app --factory --host 0.0.0.0 --port 8080"]
