"""Retry deletion of Mistral files that could not be removed inline."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingRemoteFile:
    cleanup_id: str
    provider_file_id: str
    delete_attempts: int


class RemoteFileRepository(Protocol):
    def pending_remote_files(self, *, now: datetime) -> list[PendingRemoteFile]: ...

    def remove_remote_file(self, *, provider_file_id: str) -> None: ...

    def reschedule_remote_file(
        self, *, provider_file_id: str, next_attempt_at: datetime
    ) -> None: ...


class MistralFileDeleter(Protocol):
    def delete_file(self, file_id: str) -> Awaitable[None]: ...


async def cleanup_remote_files(
    repository: RemoteFileRepository, deleter: MistralFileDeleter, now: datetime
) -> None:
    """Delete due Mistral files, retaining only failed cleanup records."""
    for record in repository.pending_remote_files(now=now):
        try:
            await deleter.delete_file(record.provider_file_id)
        except Exception as exc:  # noqa: BLE001 - provider SDK exception types are not stable
            logger.warning(
                "mistral_remote_file_cleanup_failed",
                extra={"error_type": type(exc).__name__, "attempt": record.delete_attempts + 1},
            )
            delay = min(30 * (2**record.delete_attempts), 3600)
            repository.reschedule_remote_file(
                provider_file_id=record.provider_file_id,
                next_attempt_at=now + timedelta(seconds=delay),
            )
        else:
            repository.remove_remote_file(provider_file_id=record.provider_file_id)
