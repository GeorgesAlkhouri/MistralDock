from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mistraldock.services.remote_cleanup import PendingRemoteFile, cleanup_remote_files

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.removed: list[str] = []
        self.rescheduled: list[tuple[str, datetime]] = []

    def pending_remote_files(self, *, now: datetime) -> list[PendingRemoteFile]:
        assert now is NOW
        return [PendingRemoteFile("cleanup-1", "file-1", delete_attempts=0)]

    def remove_remote_file(self, *, provider_file_id: str) -> None:
        self.removed.append(provider_file_id)

    def reschedule_remote_file(
        self, *, provider_file_id: str, next_attempt_at: datetime
    ) -> None:
        self.rescheduled.append((provider_file_id, next_attempt_at))


class DeletingMistral:
    async def delete_file(self, _: str) -> None:
        return None


class FailingMistral:
    async def delete_file(self, _: str) -> None:
        raise RuntimeError("temporary provider failure")


@pytest.mark.asyncio
async def test_cleanup_removes_successfully_deleted_remote_file() -> None:
    repository = FakeRepository()

    await cleanup_remote_files(repository, DeletingMistral(), NOW)

    assert repository.removed == ["file-1"]
    assert repository.rescheduled == []


@pytest.mark.asyncio
async def test_cleanup_reschedules_failed_delete_with_backoff() -> None:
    repository = FakeRepository()

    await cleanup_remote_files(repository, FailingMistral(), NOW)

    assert repository.removed == []
    assert repository.rescheduled == [("file-1", NOW + timedelta(seconds=30))]
