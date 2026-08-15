from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from mistraldock.clients.mistral import OCRChunkResult, UploadedChunk
from mistraldock.clients.paperless import PaperlessDocument
from mistraldock.config import WriteMode
from mistraldock.models import JobState, TriggerKind
from mistraldock.repository import ClaimedJob
from mistraldock.services.processor import DocumentProcessor, ProcessorDependencies
from mistraldock.services.validation import DocumentMetadata

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _pdf_bytes(tmp_path: Path) -> bytes:
    source = tmp_path / "fixture.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with source.open("wb") as handle:
        writer.write(handle)
    return source.read_bytes()


class FakePaperless:
    def __init__(self, document_snapshots: list[PaperlessDocument], pdf: bytes) -> None:
        self._document_snapshots = iter(document_snapshots)
        self._pdf = pdf
        self.patch_payloads: list[dict[str, object]] = []

    async def get_document(self, _: int) -> PaperlessDocument:
        return next(self._document_snapshots)

    async def list_tags(self) -> dict[str, int]:
        return {"Telekommunikation": 9}

    async def download_original(self, _: PaperlessDocument, destination: Path) -> None:
        destination.write_bytes(self._pdf)

    async def patch_document(self, _: PaperlessDocument, payload: dict[str, object]) -> None:
        self.patch_payloads.append(payload)


class FakeMistral:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def upload_chunk(self, chunk: object) -> UploadedChunk:
        return UploadedChunk("file-1", "https://signed.example/file-1", chunk)  # type: ignore[arg-type]

    async def ocr_uploaded(self, _: UploadedChunk, __: list[str]) -> OCRChunkResult:
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


def _document(modified: str = "2026-08-15T12:00:00Z") -> PaperlessDocument:
    return PaperlessDocument(42, frozenset({4}), modified, "scan.pdf", 7)


@pytest.mark.asyncio
async def test_processor_applies_one_versioned_patch_in_live_mode(tmp_path: Path) -> None:
    paperless = FakePaperless([_document(), _document()], _pdf_bytes(tmp_path))
    mistral = FakeMistral()
    remote_files = FakeRemoteFiles()
    dependencies = ProcessorDependencies(
        settings=SimpleNamespace(write_mode=WriteMode.LIVE),
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
async def test_processor_returns_conflict_without_patch_when_document_changed(tmp_path: Path) -> None:
    paperless = FakePaperless([_document(), _document("2026-08-15T12:05:00Z")], _pdf_bytes(tmp_path))
    dependencies = ProcessorDependencies(
        settings=SimpleNamespace(write_mode=WriteMode.LIVE),
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
