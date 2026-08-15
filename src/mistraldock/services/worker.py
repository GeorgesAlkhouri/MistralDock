"""Durable queue worker for MistralDock."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from random import uniform
from typing import Protocol

from mistraldock.models import JobState
from mistraldock.repository import ClaimedJob
from mistraldock.services.processor import (
    DocumentProcessor,
    PermanentProcessingError,
    ProcessResult,
    RetryableProcessingError,
)

logger = logging.getLogger(__name__)


class WorkerRepository(Protocol):
    def claim_due_job(self, *, now: datetime, lease_seconds: int) -> ClaimedJob | None: ...

    def schedule_retry(
        self, *, job_id: str, now: datetime, next_attempt_at: datetime, error_code: str
    ) -> None: ...

    def finish_run(
        self,
        *,
        job_id: str,
        state: JobState,
        now: datetime,
        applied: bool | None,
        error_code: str | None = None,
        proposal: dict[str, object] | None = None,
    ) -> None: ...


class Worker:
    """Claim at most one job, then save its durable outcome."""

    def __init__(
        self,
        *,
        repository: WorkerRepository,
        processor: DocumentProcessor,
        max_attempts: int,
        retry_base_seconds: int,
        retry_max_seconds: int,
        lease_seconds: int = 300,
        jitter: Callable[[int], int] | None = None,
    ) -> None:
        self._repository = repository
        self._processor = processor
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._lease_seconds = lease_seconds
        self._jitter = jitter or (lambda delay: int(uniform(0, min(delay, 30))))

    async def run_once(self, now: datetime) -> bool:
        job = self._repository.claim_due_job(now=now, lease_seconds=self._lease_seconds)
        if job is None:
            return False
        try:
            result = await self._processor.process(job)
        except RetryableProcessingError as exc:
            self._save_retry_or_failure(job, now, str(exc))
        except PermanentProcessingError as exc:
            self._repository.finish_run(
                job_id=job.job_id,
                state=JobState.FAILED,
                now=now,
                applied=False,
                error_code=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - provider SDK exception types are not stable
            logger.warning("unexpected_processing_error", extra={"error_type": type(exc).__name__})
            self._save_retry_or_failure(job, now, "unexpected_error")
        else:
            self._save_result(job, result, now)
        return True

    def _save_retry_or_failure(self, job: ClaimedJob, now: datetime, error_code: str) -> None:
        if job.attempt >= self._max_attempts:
            self._repository.finish_run(
                job_id=job.job_id,
                state=JobState.FAILED,
                now=now,
                applied=False,
                error_code=error_code,
            )
            return
        delay = min(self._retry_base_seconds * (2 ** (job.attempt - 1)), self._retry_max_seconds)
        self._repository.schedule_retry(
            job_id=job.job_id,
            now=now,
            next_attempt_at=now + timedelta(seconds=delay + self._jitter(delay)),
            error_code=error_code,
        )

    def _save_result(self, job: ClaimedJob, result: ProcessResult, now: datetime) -> None:
        self._repository.finish_run(
            job_id=job.job_id,
            state=result.state,
            now=now,
            applied=result.applied,
            error_code=result.error_code,
            proposal=_safe_proposal(result.payload),
        )


def _safe_proposal(payload: dict[str, object] | None) -> dict[str, object] | None:
    if payload is None:
        return None
    return {key: value for key, value in payload.items() if key != "content"}
