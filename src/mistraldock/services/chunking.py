"""Create bounded OCR inputs without altering Paperless source files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter

_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class UnsupportedDocumentError(ValueError):
    """The original file cannot be safely sent to Mistral in v1."""


@dataclass(frozen=True)
class DocumentChunk:
    path: Path
    page_numbers: tuple[int, ...]
    media_type: str
    is_temporary: bool


def chunk_document(source: Path, destination: Path, max_pages: int = 8) -> list[DocumentChunk]:
    """Return at most eight-page PDF chunks or one supported image chunk."""
    suffix = source.suffix.casefold()
    if suffix in _IMAGE_MEDIA_TYPES:
        return [DocumentChunk(source, (0,), _IMAGE_MEDIA_TYPES[suffix], is_temporary=False)]
    if suffix != ".pdf":
        raise UnsupportedDocumentError(f"unsupported_document_type:{suffix or 'unknown'}")
    try:
        reader = PdfReader(source)
    except Exception as exc:
        raise UnsupportedDocumentError("unreadable_pdf") from exc
    if reader.is_encrypted:
        raise UnsupportedDocumentError("encrypted_pdf")
    if len(reader.pages) == 0:
        raise UnsupportedDocumentError("empty_pdf")
    destination.mkdir(parents=True, exist_ok=True)
    chunks: list[DocumentChunk] = []
    for start in range(0, len(reader.pages), max_pages):
        page_numbers = tuple(range(start, min(start + max_pages, len(reader.pages))))
        writer = PdfWriter()
        for page_number in page_numbers:
            writer.add_page(reader.pages[page_number])
        path = destination / f"chunk-{start + 1:06d}.pdf"
        with path.open("wb") as handle:
            writer.write(handle)
        chunks.append(DocumentChunk(path, page_numbers, "application/pdf", is_temporary=True))
    return chunks
