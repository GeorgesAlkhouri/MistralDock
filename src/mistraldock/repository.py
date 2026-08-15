"""Transactional persistence for jobs, runs, and remote-file cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from mistraldock.models import JobRecord, JobState, RemoteFileRecord, RunRecord, TriggerKind

_ACTIVE_STATES = {JobState.QUEUED, JobState.PROCESSING, JobState.RETRY_WAIT}


@dataclass(frozen=True)
class QueuedJob:
    job_id: str
    document_id: int
    state: JobState


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    run_id: str
    document_id: int
    trigger: TriggerKind
    attempt: int


class JobRepository:
    """Use one session per worker/API request for durable state changes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_automatic(self, *, document_id: int, now: datetime) -> QueuedJob:
        return self._enqueue(document_id=document_id, trigger=TriggerKind.AUTOMATIC, now=now, force=False)

    def enqueue_reprocess(self, *, document_id: int, now: datetime) -> QueuedJob:
        return self._enqueue(document_id=document_id, trigger=TriggerKind.REPROCESS, now=now, force=True)

    def _enqueue(self, *, document_id: int, trigger: TriggerKind, now: datetime, force: bool) -> QueuedJob:
        record = self._find_by_document_id(document_id)
        if record is None:
            record = JobRecord(
                id=str(uuid4()),
                document_id=document_id,
                trigger=trigger.value,
                state=JobState.QUEUED.value,
                attempt_count=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            self._session.add(record)
        elif force and JobState(record.state) not in _ACTIVE_STATES:
            record.trigger = trigger.value
            record.state = JobState.QUEUED.value
            record.attempt_count = 0
            record.next_attempt_at = now
            record.lease_until = None
            record.current_run_id = None
            record.last_error_code = None
            record.updated_at = now
        self._session.commit()
        return QueuedJob(record.id, record.document_id, JobState(record.state))

    def claim_due_job(self, *, now: datetime, lease_seconds: int) -> ClaimedJob | None:
        statement: Select[tuple[JobRecord]] = (
            select(JobRecord)
            .where(JobRecord.state.in_([JobState.QUEUED.value, JobState.RETRY_WAIT.value]))
            .where(JobRecord.next_attempt_at <= now)
            .order_by(JobRecord.next_attempt_at, JobRecord.created_at)
            .limit(1)
        )
        record = self._session.execute(statement).scalar_one_or_none()
        if record is None:
            return None
        record.state = JobState.PROCESSING.value
        record.attempt_count += 1
        record.lease_until = now + timedelta(seconds=lease_seconds)
        record.updated_at = now
        run = RunRecord(
            id=str(uuid4()),
            job_id=record.id,
            document_id=record.document_id,
            trigger=record.trigger,
            attempt=record.attempt_count,
            state=JobState.PROCESSING.value,
            started_at=now,
        )
        self._session.add(run)
        record.current_run_id = run.id
        self._session.commit()
        return ClaimedJob(record.id, run.id, record.document_id, TriggerKind(record.trigger), record.attempt_count)

    def finish_run(
        self,
        *,
        job_id: str,
        state: JobState,
        now: datetime,
        applied: bool | None,
        error_code: str | None = None,
        proposal: dict[str, object] | None = None,
    ) -> None:
        record = self._session.get(JobRecord, job_id)
        if record is None:
            raise KeyError(job_id)
        record.state = state.value
        record.lease_until = None
        record.last_error_code = error_code
        record.updated_at = now
        if record.current_run_id is not None:
            run = self._session.get(RunRecord, record.current_run_id)
            if run is not None:
                run.state = state.value
                run.finished_at = now
                run.applied = applied
                run.error_code = error_code
                run.proposal = proposal
        self._session.commit()

    def schedule_retry(self, *, job_id: str, now: datetime, next_attempt_at: datetime, error_code: str) -> None:
        record = self._require_job(job_id)
        record.state = JobState.RETRY_WAIT.value
        record.lease_until = None
        record.next_attempt_at = next_attempt_at
        record.last_error_code = error_code
        record.updated_at = now
        self._finish_current_run(record, JobState.RETRY_WAIT.value, now, None, error_code)
        self._session.commit()

    def release_expired_leases(self, *, now: datetime) -> int:
        records = self._session.execute(
            select(JobRecord)
            .where(JobRecord.state == JobState.PROCESSING.value)
            .where(JobRecord.lease_until.is_not(None))
            .where(JobRecord.lease_until <= now)
        ).scalars()
        count = 0
        for record in records:
            record.state = JobState.RETRY_WAIT.value
            record.lease_until = None
            record.next_attempt_at = now
            record.last_error_code = "lease_expired"
            record.updated_at = now
            self._finish_current_run(record, "interrupted", now, None, "lease_expired")
            count += 1
        self._session.commit()
        return count

    def get_job(self, job_id: str) -> QueuedJob:
        record = self._require_job(job_id)
        return QueuedJob(record.id, record.document_id, JobState(record.state))

    def count_runs(self, document_id: int) -> int:
        return len(
            self._session.execute(select(RunRecord).where(RunRecord.document_id == document_id)).scalars().all()
        )

    def list_runs(self, document_id: int) -> list[dict[str, object]]:
        records = self._session.execute(
            select(RunRecord)
            .where(RunRecord.document_id == document_id)
            .order_by(RunRecord.started_at.desc())
        ).scalars()
        return [
            {
                "run_id": record.id,
                "state": record.state,
                "trigger": record.trigger,
                "attempt": record.attempt,
                "started_at": record.started_at.isoformat(),
                "finished_at": record.finished_at.isoformat() if record.finished_at else None,
                "applied": record.applied,
                "error_code": record.error_code,
                "proposal": record.proposal,
                "page_count": record.page_count,
                "chunk_count": record.chunk_count,
            }
            for record in records
        ]

    def _find_by_document_id(self, document_id: int) -> JobRecord | None:
        return self._session.execute(
            select(JobRecord).where(JobRecord.document_id == document_id)
        ).scalar_one_or_none()

    def _require_job(self, job_id: str) -> JobRecord:
        record = self._session.get(JobRecord, job_id)
        if record is None:
            raise KeyError(job_id)
        return record

    def _finish_current_run(
        self, record: JobRecord, state: str, now: datetime, applied: bool | None, error_code: str | None
    ) -> None:
        if record.current_run_id is None:
            return
        run = self._session.get(RunRecord, record.current_run_id)
        if run is None:
            return
        run.state = state
        run.finished_at = now
        run.applied = applied
        run.error_code = error_code

    def register_remote_file(self, *, run_id: str, provider_file_id: str, now: datetime) -> str:
        record = RemoteFileRecord(
            id=str(uuid4()),
            run_id=run_id,
            provider_file_id=provider_file_id,
            delete_attempts=0,
            next_attempt_at=now,
            created_at=now,
        )
        self._session.add(record)
        self._session.commit()
        return record.id

    def remove_remote_file(self, *, provider_file_id: str) -> None:
        record = self._session.execute(
            select(RemoteFileRecord).where(RemoteFileRecord.provider_file_id == provider_file_id)
        ).scalar_one_or_none()
        if record is not None:
            self._session.delete(record)
            self._session.commit()

    def close(self) -> None:
        self._session.close()
