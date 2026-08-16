FROM ghcr.io/astral-sh/uv:0.7.3@sha256:87a04222b228501907f487b338ca6fc1514a93369bfce6930eb06c8d576e58a4 AS uv

FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv sync --frozen --no-dev

RUN groupadd --system --gid 10001 mistraldock \
    && useradd --system --uid 10001 --gid mistraldock --home /nonexistent --shell /usr/sbin/nologin mistraldock \
    && mkdir /data \
    && chown mistraldock:mistraldock /data

USER mistraldock

EXPOSE 8080

CMD ["sh", "-c", ".venv/bin/alembic upgrade head && .venv/bin/uvicorn mistraldock.api:create_app --factory --host 0.0.0.0 --port 8080"]
