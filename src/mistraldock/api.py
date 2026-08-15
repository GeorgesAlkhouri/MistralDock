"""Authenticated HTTP API and process lifecycle."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest
from pydantic import BaseModel, PositiveInt

from mistraldock import __version__
from mistraldock.clients.mistral import MistralClient
from mistraldock.clients.paperless import PaperlessClient
from mistraldock.config import Settings
from mistraldock.db import Database
from mistraldock.repository import JobRepository
from mistraldock.services.processor import DocumentProcessor, ProcessorDependencies
from mistraldock.services.remote_cleanup import cleanup_remote_files
from mistraldock.services.worker import Worker


class DocumentRequest(BaseModel):
    document_id: PositiveInt


class QueueResponse(BaseModel):
    job_id: str
    document_id: int
    state: str


class RunsResponse(BaseModel):
    document_id: int
    runs: list[dict[str, object]]


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.jobs_accepted = Counter(
            "mistraldock_jobs_accepted_total",
            "Jobs accepted by source",
            ["trigger"],
            registry=self.registry,
        )


def create_app(
    settings: Settings | None = None, *, database: Database | None = None, start_worker: bool = True
) -> FastAPI:
    """Create a MistralDock API instance with one optional in-process worker."""
    settings = settings or Settings()
    database = database or Database(settings.database_url)
    metrics = Metrics()
    paperless = PaperlessClient(
        str(settings.paperless_url),
        settings.paperless_token.get_secret_value(),
        api_version=settings.paperless_api_version,
    )
    mistral = MistralClient(
        settings.mistral_api_key.get_secret_value(),
        ocr_model=settings.mistral_ocr_model,
        metadata_model=settings.mistral_metadata_model,
    )
    stop = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.create_schema()
        worker_task = (
            asyncio.create_task(_worker_loop(database, settings, paperless, mistral, stop))
            if start_worker
            else None
        )
        try:
            yield
        finally:
            stop.set()
            if worker_task is not None:
                await worker_task
            await paperless.aclose()

    app = FastAPI(title="MistralDock", version=__version__, lifespan=lifespan)
    app.state.database = database

    async def require_api_token(request: Request) -> None:
        received = request.headers.get("Authorization", "")
        expected = f"Bearer {settings.mistraldock_api_token.get_secret_value()}"
        if not secrets.compare_digest(received, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")

    @app.post(
        "/v1/webhooks/paperless",
        response_model=QueueResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_api_token)],
    )
    async def paperless_webhook(payload: DocumentRequest) -> QueueResponse:
        repository = JobRepository(database.session_factory())
        try:
            job = repository.enqueue_automatic(document_id=payload.document_id, now=datetime.now(UTC))
        finally:
            repository.close()
        metrics.jobs_accepted.labels(trigger="automatic").inc()
        return QueueResponse(job_id=job.job_id, document_id=job.document_id, state=job.state.value)

    @app.post(
        "/v1/documents/{document_id}/reprocess",
        response_model=QueueResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_api_token)],
    )
    async def reprocess_document(document_id: PositiveInt) -> QueueResponse:
        repository = JobRepository(database.session_factory())
        try:
            job = repository.enqueue_reprocess(document_id=document_id, now=datetime.now(UTC))
        finally:
            repository.close()
        metrics.jobs_accepted.labels(trigger="reprocess").inc()
        return QueueResponse(job_id=job.job_id, document_id=job.document_id, state=job.state.value)

    @app.get(
        "/v1/documents/{document_id}/runs",
        response_model=RunsResponse,
        dependencies=[Depends(require_api_token)],
    )
    async def document_runs(document_id: PositiveInt) -> RunsResponse:
        repository = JobRepository(database.session_factory())
        try:
            runs = repository.list_runs(document_id)
        finally:
            repository.close()
        return RunsResponse(document_id=document_id, runs=runs)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        with database.engine.connect():
            pass
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    return app


async def _worker_loop(
    database: Database,
    settings: Settings,
    paperless: PaperlessClient,
    mistral: MistralClient,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        repository = JobRepository(database.session_factory())
        try:
            now = datetime.now(UTC)
            await cleanup_remote_files(repository, mistral, now)
            processor = DocumentProcessor(
                ProcessorDependencies(
                    settings=settings,
                    paperless=paperless,
                    mistral=mistral,
                    remote_files=repository,
                    workspace_root=Path("/tmp"),
                    now=lambda: datetime.now(UTC),
                )
            )
            worker = Worker(
                repository=repository,
                processor=processor,
                max_attempts=settings.max_attempts,
                retry_base_seconds=settings.retry_base_seconds,
                retry_max_seconds=settings.retry_max_seconds,
            )
            worked = await worker.run_once(now)
        finally:
            repository.close()
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.1 if worked else 1.0)
        except TimeoutError:
            continue
