from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mistralai.client.errors import MistralError
from pypdf import PdfWriter

from mistraldock.clients.mistral import OCRChunkResult, UploadedChunk
from mistraldock.clients.paperless import PaperlessDocument
from mistraldock.config import WriteMode
from mistraldock.models import JobState, TriggerKind
from mistraldock.repository import ClaimedJob
from mistraldock.services.processor import (
    DocumentProcessor,
    PermanentProcessingError,
    ProcessorDependencies,
    RetryableProcessingError,
)
from mistraldock.services.validation import DocumentMetadata

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _pdf_bytes(tmp_path: Path) -> bytes:
    source = tmp_path / "fixture.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with source.open("wb") as handle:
        writer.write(handle)
    return source.read_bytes()


def _multi_page_pdf_bytes(tmp_path: Path, page_count: int) -> bytes:
    source = tmp_path / "multi-page-fixture.pdf"
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    with source.open("wb") as handle:
        writer.write(handle)
    return source.read_bytes()


class FakePaperless:
    def __init__(
        self,
        document_snapshots: list[PaperlessDocument],
        pdf: bytes,
        *,
        tags_by_owner: dict[int, dict[str, int]] | None = None,
    ) -> None:
        self._document_snapshots = iter(document_snapshots)
        self._pdf = pdf
        self._tags_by_owner = tags_by_owner or {17: {"Telekommunikation": 9}}
        self.tag_owner_ids: list[int] = []
        self.patch_payloads: list[dict[str, object]] = []

    async def get_document(self, _: int) -> PaperlessDocument:
        return next(self._document_snapshots)

    async def list_tags(self, owner_id: int) -> dict[str, int]:
        self.tag_owner_ids.append(owner_id)
        return self._tags_by_owner.get(owner_id, {})

    async def download_original(self, _: PaperlessDocument, destination: Path) -> None:
        destination.write_bytes(self._pdf)

    async def patch_document(self, _: PaperlessDocument, payload: dict[str, object]) -> None:
        self.patch_payloads.append(payload)


class FakeMistral:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.ocr_vocabularies: list[list[str]] = []

    async def upload_chunk(self, chunk: object) -> UploadedChunk:
        return UploadedChunk("file-1", "https://signed.example/file-1", chunk)  # type: ignore[arg-type]

    async def ocr_uploaded(self, _: UploadedChunk, vocabulary: list[str]) -> OCRChunkResult:
        self.ocr_vocabularies.append(vocabulary)
        return OCRChunkResult(
            markdown="Kabelrechnung für Juli 2026",
            metadata=DocumentMetadata(
                title="Vodafone – Kabelrechnung Juli 2026",
                created="2026-07-01",
                tags=["Telekommunikation"],
            ),
            page_count=1,
        )

    async def consolidate_metadata(self, _: list[dict[str, object]], __: list[str]) -> DocumentMetadata:
        raise AssertionError("one-page document must not be consolidated")

    async def delete_file(self, file_id: str) -> None:
        self.deleted.append(file_id)


class FakeRemoteFiles:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.removed: list[str] = []

    def register_remote_file(self, *, run_id: str, provider_file_id: str, now: datetime) -> str:
        self.registered.append(f"{run_id}:{provider_file_id}:{now.isoformat()}")
        return "cleanup-1"

    def remove_remote_file(self, *, provider_file_id: str) -> None:
        self.removed.append(provider_file_id)


class GenericTitleMistral(FakeMistral):
    async def ocr_uploaded(self, _: UploadedChunk, vocabulary: list[str]) -> OCRChunkResult:
        self.ocr_vocabularies.append(vocabulary)
        return OCRChunkResult(
            markdown="Kabelrechnung für Juli 2026",
            metadata=DocumentMetadata(title="Rechnung", created=None, tags=[]),
            page_count=1,
        )


class ForbiddenPaperless(FakePaperless):
    async def get_document(self, _: int) -> PaperlessDocument:
        request = httpx.Request("GET", "https://paperless.example/api/documents/42/")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("forbidden", request=request, response=response)


class UnauthorizedMistral(FakeMistral):
    async def ocr_uploaded(self, _: UploadedChunk, vocabulary: list[str]) -> OCRChunkResult:
        self.ocr_vocabularies.append(vocabulary)
        request = httpx.Request("POST", "https://api.mistral.ai/v1/ocr")
        response = httpx.Response(401, request=request)
        raise MistralError("unauthorized", response)


class UnavailableMistral(FakeMistral):
    async def ocr_uploaded(self, _: UploadedChunk, vocabulary: list[str]) -> OCRChunkResult:
        self.ocr_vocabularies.append(vocabulary)
        request = httpx.Request("POST", "https://api.mistral.ai/v1/ocr")
        response = httpx.Response(503, request=request)
        raise MistralError("unavailable", response)


class MultiChunkMistral(FakeMistral):
    def __init__(self) -> None:
        super().__init__()
        self.ocr_calls = 0
        self.consolidated = False
        self.consolidation_vocabularies: list[list[str]] = []

    async def ocr_uploaded(self, _: UploadedChunk, vocabulary: list[str]) -> OCRChunkResult:
        self.ocr_vocabularies.append(vocabulary)
        self.ocr_calls += 1
        return OCRChunkResult(
            markdown=f"OCR Seite {self.ocr_calls}",
            metadata=DocumentMetadata(title=f"Teil {self.ocr_calls}", created=None, tags=[]),
            page_count=1,
        )

    async def consolidate_metadata(
        self, _: list[dict[str, object]], vocabulary: list[str]
    ) -> DocumentMetadata:
        self.consolidated = True
        self.consolidation_vocabularies.append(vocabulary)
        return DocumentMetadata(
            title="Vodafone – Kabelrechnung Juli 2026",
            created="2026-07-01",
            tags=["Telekommunikation"],
        )


def _document(
    modified: str = "2026-08-15T12:00:00Z", *, owner_id: int | None = 17
) -> PaperlessDocument:
    return PaperlessDocument(
        document_id=42,
        tags=frozenset({4}),
        owner_id=owner_id,
        modified=modified,
        original_filename="scan.pdf",
        version_id=7,
    )


def _settings(write_mode: WriteMode, *, ocr_chunk_pages: int = 8) -> SimpleNamespace:
    return SimpleNamespace(write_mode=write_mode, ocr_chunk_pages=ocr_chunk_pages)


@pytest.mark.asyncio
async def test_processor_applies_one_versioned_patch_in_live_mode(tmp_path: Path) -> None:
    paperless = FakePaperless([_document(), _document()], _pdf_bytes(tmp_path))
    mistral = FakeMistral()
    remote_files = FakeRemoteFiles()
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.LIVE),
        paperless=paperless,
        mistral=mistral,
        remote_files=remote_files,
        workspace_root=tmp_path,
        now=lambda: NOW,
    )

    result = await DocumentProcessor(dependencies).process(
        ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)
    )

    assert result.state is JobState.SUCCEEDED
    assert result.applied is True
    assert paperless.patch_payloads == [
        {
            "content": "Kabelrechnung für Juli 2026",
            "title": "Vodafone – Kabelrechnung Juli 2026",
            "created": "2026-07-01",
            "tags": [4, 9],
        }
    ]
    assert mistral.deleted == ["file-1"]
    assert remote_files.removed == ["file-1"]


@pytest.mark.asyncio
async def test_processor_sends_only_document_owner_tags_to_mistral(tmp_path: Path) -> None:
    paperless = FakePaperless(
        [_document(), _document()],
        _pdf_bytes(tmp_path),
        tags_by_owner={
            17: {"Telekommunikation": 9},
            23: {"Other user's tag": 10},
        },
    )
    mistral = FakeMistral()
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.DRY_RUN),
        paperless=paperless,
        mistral=mistral,
        remote_files=FakeRemoteFiles(),
        workspace_root=tmp_path,
        now=lambda: NOW,
    )

    result = await DocumentProcessor(dependencies).process(
        ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)
    )

    assert result.payload is not None
    assert result.payload["tags"] == [4, 9]
    assert mistral.ocr_vocabularies == [["Telekommunikation"]]
    assert paperless.tag_owner_ids == [17, 17]


@pytest.mark.asyncio
async def test_processor_skips_tag_lookup_for_ownerless_document(tmp_path: Path) -> None:
    paperless = FakePaperless(
        [_document(owner_id=None), _document(owner_id=None)], _pdf_bytes(tmp_path)
    )
    mistral = FakeMistral()
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.DRY_RUN),
        paperless=paperless,
        mistral=mistral,
        remote_files=FakeRemoteFiles(),
        workspace_root=tmp_path,
        now=lambda: NOW,
    )

    result = await DocumentProcessor(dependencies).process(
        ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)
    )

    assert result.payload is not None
    assert result.payload["tags"] == [4]
    assert mistral.ocr_vocabularies == [[]]
    assert paperless.tag_owner_ids == []


@pytest.mark.asyncio
async def test_processor_returns_conflict_without_patch_when_document_changed(tmp_path: Path) -> None:
    paperless = FakePaperless([_document(), _document("2026-08-15T12:05:00Z")], _pdf_bytes(tmp_path))
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.LIVE),
        paperless=paperless,
        mistral=FakeMistral(),
        remote_files=FakeRemoteFiles(),
        workspace_root=tmp_path,
        now=lambda: NOW,
    )

    result = await DocumentProcessor(dependencies).process(
        ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)
    )

    assert result.state is JobState.CONFLICT
    assert result.applied is False
    assert paperless.patch_payloads == []


@pytest.mark.asyncio
async def test_processor_returns_conflict_when_document_owner_changes(tmp_path: Path) -> None:
    paperless = FakePaperless(
        [_document(owner_id=17), _document(owner_id=23)], _pdf_bytes(tmp_path)
    )
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.LIVE),
        paperless=paperless,
        mistral=FakeMistral(),
        remote_files=FakeRemoteFiles(),
        workspace_root=tmp_path,
        now=lambda: NOW,
    )

    result = await DocumentProcessor(dependencies).process(
        ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)
    )

    assert result.state is JobState.CONFLICT
    assert result.applied is False
    assert paperless.patch_payloads == []
    assert paperless.tag_owner_ids == [17]


@pytest.mark.asyncio
async def test_processor_marks_invalid_metadata_as_permanent_failure(tmp_path: Path) -> None:
    paperless = FakePaperless([_document(), _document()], _pdf_bytes(tmp_path))
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.DRY_RUN),
        paperless=paperless,
        mistral=GenericTitleMistral(),
        remote_files=FakeRemoteFiles(),
        workspace_root=tmp_path,
        now=lambda: NOW,
    )
    processor = DocumentProcessor(dependencies)
    job = ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)

    with pytest.raises(PermanentProcessingError, match="title"):
        await processor.process(job)


@pytest.mark.asyncio
async def test_processor_marks_paperless_forbidden_as_permanent_failure(tmp_path: Path) -> None:
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.DRY_RUN),
        paperless=ForbiddenPaperless([], b""),
        mistral=FakeMistral(),
        remote_files=FakeRemoteFiles(),
        workspace_root=tmp_path,
        now=lambda: NOW,
    )
    processor = DocumentProcessor(dependencies)
    job = ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)

    with pytest.raises(PermanentProcessingError, match="paperless_http_403"):
        await processor.process(job)


@pytest.mark.asyncio
async def test_processor_marks_mistral_unauthorized_as_permanent_failure(tmp_path: Path) -> None:
    paperless = FakePaperless([_document()], _pdf_bytes(tmp_path))
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.DRY_RUN),
        paperless=paperless,
        mistral=UnauthorizedMistral(),
        remote_files=FakeRemoteFiles(),
        workspace_root=tmp_path,
        now=lambda: NOW,
    )
    processor = DocumentProcessor(dependencies)
    job = ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)

    with pytest.raises(PermanentProcessingError, match="mistral_http_401"):
        await processor.process(job)


@pytest.mark.asyncio
async def test_processor_retries_mistral_server_errors(tmp_path: Path) -> None:
    paperless = FakePaperless([_document()], _pdf_bytes(tmp_path))
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.DRY_RUN),
        paperless=paperless,
        mistral=UnavailableMistral(),
        remote_files=FakeRemoteFiles(),
        workspace_root=tmp_path,
        now=lambda: NOW,
    )
    processor = DocumentProcessor(dependencies)
    job = ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)

    with pytest.raises(RetryableProcessingError, match="mistral_http_503"):
        await processor.process(job)


@pytest.mark.asyncio
async def test_processor_uses_configured_chunk_page_count(tmp_path: Path) -> None:
    paperless = FakePaperless([_document(), _document()], _multi_page_pdf_bytes(tmp_path, page_count=2))
    mistral = MultiChunkMistral()
    dependencies = ProcessorDependencies(
        settings=_settings(WriteMode.DRY_RUN, ocr_chunk_pages=1),
        paperless=paperless,
        mistral=mistral,
        remote_files=FakeRemoteFiles(),
        workspace_root=tmp_path,
        now=lambda: NOW,
    )

    result = await DocumentProcessor(dependencies).process(
        ClaimedJob("job-1", "run-1", 42, TriggerKind.AUTOMATIC, 1)
    )

    assert result.state is JobState.SUCCEEDED
    assert mistral.ocr_calls == 2
    assert mistral.consolidated is True
    assert mistral.ocr_vocabularies == [["Telekommunikation"], ["Telekommunikation"]]
    assert mistral.consolidation_vocabularies == [["Telekommunikation"]]
