"""Native Mistral Document AI adapter."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from mistralai import Mistral

from mistraldock.services.chunking import DocumentChunk
from mistraldock.services.metadata import (
    annotation_prompt,
    consolidation_prompt,
    metadata_response_format,
)
from mistraldock.services.validation import DocumentMetadata

logger = logging.getLogger(__name__)


class MistralProtocolError(ValueError):
    """A Mistral response lacks valid OCR or structured metadata."""


@dataclass(frozen=True)
class UploadedChunk:
    file_id: str
    signed_url: str
    chunk: DocumentChunk


@dataclass(frozen=True)
class OCRChunkResult:
    markdown: str
    metadata: DocumentMetadata
    page_count: int


class MistralClient:
    """Upload chunks, OCR with annotations, consolidate, and delete files."""

    def __init__(self, api_key: str, *, ocr_model: str, metadata_model: str, sdk: Any | None = None) -> None:
        self._sdk = sdk or Mistral(api_key=api_key)
        self._ocr_model = ocr_model
        self._metadata_model = metadata_model

    async def upload_chunk(self, chunk: DocumentChunk) -> UploadedChunk:
        uploaded = await self._sdk.files.upload_async(
            file={"file_name": chunk.path.name, "content": chunk.path.read_bytes()}, purpose="ocr"
        )
        file_id = getattr(uploaded, "id", None)
        if not isinstance(file_id, str) or not file_id:
            raise MistralProtocolError("missing_uploaded_file_id")
        signed = await self._sdk.files.get_signed_url_async(file_id=file_id)
        signed_url = getattr(signed, "url", None)
        if not isinstance(signed_url, str) or not signed_url:
            raise MistralProtocolError("missing_signed_url")
        return UploadedChunk(file_id, signed_url, chunk)

    async def ocr_uploaded(self, uploaded: UploadedChunk, vocabulary: list[str]) -> OCRChunkResult:
        response = await self._sdk.ocr.process_async(
            model=self._ocr_model,
            document={"type": "document_url", "document_url": uploaded.signed_url},
            document_annotation_format=metadata_response_format(vocabulary),
            document_annotation_prompt=annotation_prompt(vocabulary),
            include_image_base64=False,
            table_format="markdown",
        )
        pages = getattr(response, "pages", None)
        annotation = getattr(response, "document_annotation", None)
        logger.info(
            "mistral_ocr_response_received page_count=%d has_annotation=%s",
            len(pages) if isinstance(pages, list) else 0,
            str(isinstance(annotation, str)).lower(),
        )
        if not isinstance(pages, list) or not pages:
            raise MistralProtocolError("missing_ocr_pages")
        markdown_pages = [getattr(page, "markdown", None) for page in pages]
        if not all(isinstance(markdown, str) for markdown in markdown_pages):
            raise MistralProtocolError("invalid_ocr_markdown")
        if not isinstance(annotation, str):
            raise MistralProtocolError("missing_document_annotation")
        try:
            metadata = DocumentMetadata.model_validate_json(annotation)
        except ValueError as exc:
            raise MistralProtocolError("invalid_document_annotation") from exc
        return OCRChunkResult("\n\n".join(markdown_pages), metadata, len(markdown_pages))

    async def consolidate_metadata(
        self, candidates: list[dict[str, object]], vocabulary: list[str]
    ) -> DocumentMetadata:
        response = await self._sdk.chat.complete_async(
            model=self._metadata_model,
            temperature=0,
            messages=[
                {"role": "system", "content": consolidation_prompt(vocabulary)},
                {"role": "user", "content": json.dumps(candidates, ensure_ascii=False)},
            ],
            response_format=metadata_response_format(vocabulary),
        )
        try:
            content = response.choices[0].message.content
            if not isinstance(content, str):
                raise TypeError("non-string structured response")
            return DocumentMetadata.model_validate_json(content)
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise MistralProtocolError("invalid_consolidation") from exc

    async def delete_file(self, file_id: str) -> None:
        await self._sdk.files.delete_async(file_id=file_id)
