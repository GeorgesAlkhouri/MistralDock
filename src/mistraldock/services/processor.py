"""One safe, end-to-end Paperless document processing attempt."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from mistraldock.clients.mistral import OCRChunkResult, UploadedChunk
from mistraldock.clients.paperless import PaperlessDocument
from mistraldock.config import Settings, WriteMode
from mistraldock.models import JobState
from mistraldock.repository import ClaimedJob
from mistraldock.services.chunking import DocumentChunk, chunk_document
from mistraldock.services.validation import DocumentMetadata, build_validated_update

_MISTRAL_MAX_FILE_BYTES = 512 * 1024 * 1024
logger = logging.getLogger(__name__)


class ProcessingError(RuntimeError):
    """A classified processing failure."""


class RetryableProcessingError(ProcessingError):
    """The job may succeed when retried later."""


class PermanentProcessingError(ProcessingError):
    """Retrying without input changes cannot succeed."""


class PaperlessGateway(Protocol):
    async def get_document(self, document_id: int) -> PaperlessDocument: ...

    async def list_tags(self) -> dict[str, int]: ...

    async def download_original(self, document: PaperlessDocument, destination: Path) -> None: ...

    async def patch_document(self, document: PaperlessDocument, payload: dict[str, object]) -> None: ...


class MistralGateway(Protocol):
    async def upload_chunk(self, chunk: DocumentChunk) -> UploadedChunk: ...

    async def ocr_uploaded(self, uploaded: UploadedChunk, vocabulary: list[str]) -> OCRChunkResult: ...

    async def consolidate_metadata(
        self, candidates: list[dict[str, object]], vocabulary: list[str]
    ) -> DocumentMetadata: ...

    async def delete_file(self, file_id: str) -> None: ...


class RemoteFileStore(Protocol):
    def register_remote_file(self, *, run_id: str, provider_file_id: str, now: datetime) -> str: ...

    def remove_remote_file(self, *, provider_file_id: str) -> None: ...


@dataclass(frozen=True)
class ProcessorDependencies:
    settings: Settings
    paperless: PaperlessGateway
    mistral: MistralGateway
    remote_files: RemoteFileStore
    workspace_root: Path
    now: Callable[[], datetime]


@dataclass(frozen=True)
class ProcessResult:
    state: JobState
    applied: bool
    payload: dict[str, object] | None = None
    error_code: str | None = None
    warnings: tuple[str, ...] = ()


class DocumentProcessor:
    """Download, OCR, validate, and optionally update one claimed Paperless document."""

    def __init__(self, dependencies: ProcessorDependencies) -> None:
        self._dependencies = dependencies

    async def process(self, job: ClaimedJob) -> ProcessResult:
        original = await self._dependencies.paperless.get_document(job.document_id)
        initial_tags = await self._dependencies.paperless.list_tags()
        self._dependencies.workspace_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self._dependencies.workspace_root) as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / Path(original.original_filename).name
            await self._dependencies.paperless.download_original(original, source)
            chunks = chunk_document(source, workspace / "chunks")
            results = [await self._ocr_chunk(job, chunk, sorted(initial_tags)) for chunk in chunks]

        content = "\n\n".join(result.markdown for result in results)
        metadata = await self._metadata_for(results, chunks, sorted(initial_tags))
        fresh = await self._dependencies.paperless.get_document(job.document_id)
        if fresh.version_id != original.version_id or fresh.modified != original.modified:
            return ProcessResult(JobState.CONFLICT, applied=False, error_code="document_changed")
        current_tags = await self._dependencies.paperless.list_tags()
        update = build_validated_update(
            metadata=metadata,
            content=content,
            current_tag_ids=set(fresh.tags),
            tags_by_name=current_tags,
            original_filename=fresh.original_filename,
            today=self._dependencies.now().date(),
        )
        if self._dependencies.settings.write_mode is WriteMode.DRY_RUN:
            return ProcessResult(
                JobState.SUCCEEDED,
                applied=False,
                payload=update.payload,
                warnings=update.warnings,
            )
        await self._dependencies.paperless.patch_document(fresh, update.payload)
        return ProcessResult(
            JobState.SUCCEEDED,
            applied=True,
            payload=update.payload,
            warnings=update.warnings,
        )

    async def _ocr_chunk(
        self, job: ClaimedJob, chunk: DocumentChunk, vocabulary: list[str]
    ) -> OCRChunkResult:
        if chunk.path.stat().st_size > _MISTRAL_MAX_FILE_BYTES:
            raise PermanentProcessingError("chunk_too_large")
        uploaded = await self._dependencies.mistral.upload_chunk(chunk)
        self._dependencies.remote_files.register_remote_file(
            run_id=job.run_id, provider_file_id=uploaded.file_id, now=self._dependencies.now()
        )
        try:
            result = await self._dependencies.mistral.ocr_uploaded(uploaded, vocabulary)
        finally:
            try:
                await self._dependencies.mistral.delete_file(uploaded.file_id)
            except Exception as exc:  # noqa: BLE001 - provider exceptions are SDK-version dependent
                logger.warning(
                    "mistral_file_delete_failed",
                    extra={"provider_file_id": uploaded.file_id, "error_type": type(exc).__name__},
                )
            else:
                self._dependencies.remote_files.remove_remote_file(provider_file_id=uploaded.file_id)
        if result.page_count != len(chunk.page_numbers):
            raise PermanentProcessingError("ocr_page_count_mismatch")
        return result

    async def _metadata_for(
        self, results: list[OCRChunkResult], chunks: list[DocumentChunk], vocabulary: list[str]
    ) -> DocumentMetadata:
        if len(results) == 1:
            return results[0].metadata
        candidates = [
            {
                "page_numbers": list(chunk.page_numbers),
                "metadata": result.metadata.model_dump(mode="json"),
            }
            for chunk, result in zip(chunks, results, strict=True)
        ]
        return await self._dependencies.mistral.consolidate_metadata(candidates, vocabulary)
