from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from mistraldock.services.chunking import chunk_document


def test_pdf_is_split_into_eight_page_chunks(tmp_path: Path) -> None:
    source = tmp_path / "nine-pages.pdf"
    writer = PdfWriter()
    for _ in range(9):
        writer.add_blank_page(width=200, height=200)
    with source.open("wb") as handle:
        writer.write(handle)

    chunks = chunk_document(source, tmp_path / "chunks")

    assert [chunk.page_numbers for chunk in chunks] == [tuple(range(8)), (8,)]
    assert [chunk.path.exists() for chunk in chunks] == [True, True]


def test_supported_image_is_one_chunk_without_copying(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"png")

    chunks = chunk_document(source, tmp_path / "chunks")

    assert len(chunks) == 1
    assert chunks[0].path == source
    assert chunks[0].page_numbers == (0,)
