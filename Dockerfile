FROM docker.io/astral/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

FROM python:3.13.18-slim-trixie@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50 AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-build --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src

FROM python:3.13.18-slim-trixie@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50

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
