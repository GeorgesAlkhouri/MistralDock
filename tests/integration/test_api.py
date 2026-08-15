from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mistraldock.api import create_app
from mistraldock.config import Settings
from mistraldock.db import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        PAPERLESS_URL="https://paperless.example",
        PAPERLESS_TOKEN="paperless-token",
        MISTRAL_API_KEY="mistral-key",
        MISTRALDOCK_API_TOKEN="service-token",
        DATABASE_URL=f"sqlite:///{tmp_path / 'mistraldock.db'}",
    )


@pytest.fixture
def app(settings: Settings) -> object:
    database = Database(settings.database_url)
    database.create_schema()
    return create_app(settings, database=database, start_worker=False)


@pytest.mark.asyncio
async def test_webhook_queues_document_and_returns_202(app: object) -> None:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/webhooks/paperless",
            headers={"Authorization": "Bearer service-token"},
            json={"document_id": 42},
        )

    assert response.status_code == 202
    assert response.json()["document_id"] == 42
    assert response.json()["state"] == "queued"


@pytest.mark.asyncio
async def test_reprocess_requires_bearer_token(app: object) -> None:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/documents/42/reprocess")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_runs_endpoint_returns_queued_job_without_document_content(app: object) -> None:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    headers = {"Authorization": "Bearer service-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/v1/webhooks/paperless", headers=headers, json={"document_id": 42})
        response = await client.get("/v1/documents/42/runs", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"document_id": 42, "runs": []}


@pytest.mark.asyncio
async def test_health_and_metrics_are_available_without_authentication(app: object) -> None:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
        metrics = await client.get("/metrics")

    assert live.status_code == 200
    assert ready.status_code == 200
    assert metrics.status_code == 200
    assert "mistraldock_jobs_accepted_total" in metrics.text
