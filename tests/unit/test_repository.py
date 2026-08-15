from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mistraldock.db import Database
from mistraldock.models import JobState
from mistraldock.repository import JobRepository

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def repository() -> JobRepository:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return JobRepository(database.session_factory())


def test_automatic_enqueue_is_idempotent(repository: JobRepository) -> None:
    first = repository.enqueue_automatic(document_id=42, now=NOW)
    second = repository.enqueue_automatic(document_id=42, now=NOW)

    assert second.job_id == first.job_id
    assert repository.count_runs(42) == 0


def test_expired_processing_lease_becomes_retryable(repository: JobRepository) -> None:
    repository.enqueue_reprocess(document_id=42, now=NOW)
    claimed = repository.claim_due_job(now=NOW, lease_seconds=60)

    assert claimed is not None
    assert repository.release_expired_leases(now=NOW + timedelta(seconds=61)) == 1
    assert repository.get_job(claimed.job_id).state is JobState.RETRY_WAIT


def test_manual_reprocess_resets_terminal_job(repository: JobRepository) -> None:
    queued = repository.enqueue_automatic(document_id=42, now=NOW)
    claimed = repository.claim_due_job(now=NOW, lease_seconds=60)
    assert claimed is not None
    repository.finish_run(job_id=queued.job_id, state=JobState.SUCCEEDED, now=NOW, applied=False)

    reprocessed = repository.enqueue_reprocess(document_id=42, now=NOW + timedelta(seconds=1))

    assert reprocessed.job_id == queued.job_id
    assert repository.get_job(queued.job_id).state is JobState.QUEUED
