from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mistraldock.clients.mistral import MistralClient
from mistraldock.services.chunking import DocumentChunk


class FakeFiles:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def upload_async(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(id="file-1")

    async def get_signed_url_async(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(url="https://signed.example/file-1")

    async def delete_async(self, *, file_id: str) -> None:
        self.deleted.append(file_id)


class FakeOcr:
    def __init__(self) -> None:
        self.received_annotation_format = False

    async def process_async(self, **kwargs: object) -> SimpleNamespace:
        response_format = kwargs.get("document_annotation_format")
        if not hasattr(response_format, "json_schema"):
            raise AssertionError("Mistral OCR calls require an SDK response format")
        self.received_annotation_format = True
        return SimpleNamespace(
            pages=[SimpleNamespace(markdown="OCR page 1")],
            document_annotation='{"title":"Vodafone – Rechnung","created":"2026-07-01","tags":["Telekommunikation"]}',
        )


class FakeChat:
    async def complete_async(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"title":"Vodafone – Vertrag","created":null,"tags":["Telekommunikation"]}'
                    )
                )
            ]
        )


@pytest.mark.asyncio
async def test_mistral_ocr_returns_markdown_metadata_and_deletes_uploaded_file(tmp_path: Path) -> None:
    source = tmp_path / "chunk.pdf"
    source.write_bytes(b"pdf")
    fake_ocr = FakeOcr()
    fake = SimpleNamespace(files=FakeFiles(), ocr=fake_ocr, chat=FakeChat())
    client = MistralClient("key", ocr_model="mistral-ocr-latest", metadata_model="mistral-small-latest", sdk=fake)

    uploaded = await client.upload_chunk(DocumentChunk(source, (0,), "application/pdf", is_temporary=False))
    result = await client.ocr_uploaded(uploaded, ["Telekommunikation"])
    await client.delete_file(uploaded.file_id)

    assert result.markdown == "OCR page 1"
    assert result.metadata.title == "Vodafone – Rechnung"
    assert fake_ocr.received_annotation_format is True
    assert fake.files.deleted == ["file-1"]


@pytest.mark.asyncio
async def test_mistral_consolidates_chunk_metadata() -> None:
    fake = SimpleNamespace(files=FakeFiles(), ocr=FakeOcr(), chat=FakeChat())
    client = MistralClient("key", ocr_model="mistral-ocr-latest", metadata_model="mistral-small-latest", sdk=fake)

    result = await client.consolidate_metadata(
        [
            {"page_numbers": [0], "metadata": {"title": "Seite eins", "created": None, "tags": []}},
            {"page_numbers": [8], "metadata": {"title": "Vertrag", "created": "2026-07-01", "tags": ["Telekommunikation"]}},
        ],
        ["Telekommunikation"],
    )

    assert result.title == "Vodafone – Vertrag"
