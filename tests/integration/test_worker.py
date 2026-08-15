from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mistraldock.models import JobState, TriggerKind
from mistraldock.repository import ClaimedJob
from mistraldock.services.processor import ProcessResult, RetryableProcessingError
from mistraldock.services.worker import Worker

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.claimed = ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 2)
        self.retries: list[tuple[datetime, str]] = []
        self.finished: list[tuple[JobState, bool | None]] = []

    def claim_due_job(self, *, now: datetime, lease_seconds: int) -> ClaimedJob | None:
        assert now is NOW
        assert lease_seconds == 300
        return self.claimed

    def schedule_retry(self, *, job_id: str, now: datetime, next_attempt_at: datetime, error_code: str) -> None:
        assert job_id == "job-1"
        assert now is NOW
        self.retries.append((next_attempt_at, error_code))

    def finish_run(self, **kwargs: object) -> None:
        self.finished.append((kwargs["state"], kwargs["applied"]))


class RetryProcessor:
    async def process(self, _: ClaimedJob) -> ProcessResult:
        raise RetryableProcessingError("mistral_unavailable")


class SuccessProcessor:
    async def process(self, _: ClaimedJob) -> ProcessResult:
        return ProcessResult(JobState.SUCCEEDED, applied=False, payload={"title": "Vorschlag"})


class CrashProcessor:
    async def process(self, _: ClaimedJob) -> ProcessResult:
        raise RuntimeError("provider connection reset")


@pytest.mark.asyncio
async def test_worker_schedules_exponential_retry_for_transient_error() -> None:
    repository = FakeRepository()
    worker = Worker(
        repository=repository,
        processor=RetryProcessor(),
        max_attempts=5,
        retry_base_seconds=30,
        retry_max_seconds=3600,
        lease_seconds=300,
        jitter=lambda _: 0,
    )

    assert await worker.run_once(NOW) is True
    assert repository.retries == [(NOW + timedelta(seconds=60), "mistral_unavailable")]


@pytest.mark.asyncio
async def test_worker_finishes_successful_dry_run() -> None:
    repository = FakeRepository()
    worker = Worker(
        repository=repository,
        processor=SuccessProcessor(),
        max_attempts=5,
        retry_base_seconds=30,
        retry_max_seconds=3600,
        lease_seconds=300,
        jitter=lambda _: 0,
    )

    assert await worker.run_once(NOW) is True
    assert repository.finished == [(JobState.SUCCEEDED, False)]


@pytest.mark.asyncio
async def test_worker_retries_unclassified_provider_failure() -> None:
    repository = FakeRepository()
    worker = Worker(
        repository=repository,
        processor=CrashProcessor(),
        max_attempts=5,
        retry_base_seconds=30,
        retry_max_seconds=3600,
        lease_seconds=300,
        jitter=lambda _: 0,
    )

    assert await worker.run_once(NOW) is True
    assert repository.retries == [(NOW + timedelta(seconds=60), "unexpected_error")]
