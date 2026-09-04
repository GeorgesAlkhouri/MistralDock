from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from mistraldock.clients.paperless import PaperlessClient, PaperlessProtocolError


@pytest.mark.asyncio
async def test_paperless_lists_only_tags_owned_by_document_owner() -> None:
    router = respx.Router(assert_all_mocked=True)
    router.get("https://paperless.example/api/tags/", params={"page": "2"}).respond(
        200,
        json={
            "results": [
                {"id": 2, "name": "Contracts", "owner": 17},
                {"id": 3, "name": "Other user's tag", "owner": 23},
            ],
            "next": None,
        },
    )
    first = router.get("https://paperless.example/api/tags/").respond(
        200,
        json={
            "results": [
                {"id": 1, "name": "Invoices", "owner": 17},
                {"id": 4, "name": "Shared without owner", "owner": None},
            ],
            "next": "https://paperless.example/api/tags/?page=2",
        },
    )
    transport = httpx.MockTransport(router.async_handler)
    async with httpx.AsyncClient(transport=transport) as http_client, PaperlessClient(
        "https://paperless.example", "token", api_version=9, client=http_client
    ) as client:
        tags = await client.list_tags(17)

    assert tags == {"Invoices": 1, "Contracts": 2}
    assert first.calls.last.request.headers["Accept"] == "application/json; version=9"
    assert first.calls.last.request.headers["Authorization"] == "Token token"


@pytest.mark.asyncio
async def test_paperless_rejects_tag_pagination_to_another_origin() -> None:
    requested_urls: list[httpx.URL] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(request.url)
        return httpx.Response(
            200,
            json={"results": [], "next": "https://attacker.example/capture"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client, PaperlessClient(
        "https://paperless.example", "secret", api_version=9, client=http_client
    ) as client:
        with pytest.raises(PaperlessProtocolError, match="invalid_tag_next_origin"):
            await client.list_tags(17)

    assert requested_urls == [httpx.URL("https://paperless.example/api/tags/")]


@pytest.mark.asyncio
async def test_paperless_rejects_tag_with_invalid_owner() -> None:
    router = respx.Router(assert_all_mocked=True)
    router.get("https://paperless.example/api/tags/").respond(
        200, json={"results": [{"id": 1, "name": "Invoices", "owner": "17"}], "next": None}
    )
    transport = httpx.MockTransport(router.async_handler)
    async with httpx.AsyncClient(transport=transport) as http_client, PaperlessClient(
        "https://paperless.example", "token", api_version=9, client=http_client
    ) as client:
        with pytest.raises(PaperlessProtocolError, match="invalid_tag"):
            await client.list_tags(17)


@pytest.mark.asyncio
async def test_paperless_rejects_tag_without_owner() -> None:
    router = respx.Router(assert_all_mocked=True)
    router.get("https://paperless.example/api/tags/").respond(
        200, json={"results": [{"id": 1, "name": "Invoices"}], "next": None}
    )
    transport = httpx.MockTransport(router.async_handler)
    async with httpx.AsyncClient(transport=transport) as http_client, PaperlessClient(
        "https://paperless.example", "token", api_version=9, client=http_client
    ) as client:
        with pytest.raises(PaperlessProtocolError, match="invalid_tag"):
            await client.list_tags(17)


@pytest.mark.asyncio
async def test_paperless_rejects_document_without_owner() -> None:
    router = respx.Router(assert_all_mocked=True)
    router.get("https://paperless.example/api/documents/42/").respond(
        200,
        json={
            "id": 42,
            "tags": [4],
            "modified": "2026-08-15T12:00:00Z",
            "original_file_name": "scan.pdf",
            "versions": [{"id": 7}],
        },
    )
    transport = httpx.MockTransport(router.async_handler)
    async with httpx.AsyncClient(transport=transport) as http_client, PaperlessClient(
        "https://paperless.example", "token", api_version=9, client=http_client
    ) as client:
        with pytest.raises(PaperlessProtocolError, match="invalid_document_owner"):
            await client.get_document(42)


@pytest.mark.asyncio
async def test_paperless_downloads_original_version_and_patches_same_version(
    tmp_path: Path,
) -> None:
    router = respx.Router(assert_all_mocked=True)
    router.get("https://paperless.example/api/documents/42/").respond(
        200,
        json={
            "id": 42,
            "tags": [4],
            "owner": 17,
            "modified": "2026-08-15T12:00:00Z",
            "original_file_name": "scan.pdf",
            "versions": [{"id": 7}],
        },
    )
    download = router.get("https://paperless.example/api/documents/42/download/").respond(
        200, content=b"pdf"
    )
    patch = router.patch("https://paperless.example/api/documents/42/").respond(200, json={})
    destination = tmp_path / "original.pdf"

    transport = httpx.MockTransport(router.async_handler)
    async with httpx.AsyncClient(transport=transport) as http_client, PaperlessClient(
        "https://paperless.example", "token", api_version=9, client=http_client
    ) as client:
        document = await client.get_document(42)
        await client.download_original(document, destination)
        await client.patch_document(document, {"title": "Archivierter Titel"})

    assert destination.read_bytes() == b"pdf"
    assert download.calls.last.request.url.params == httpx.QueryParams(
        {"original": "true", "version": "7"}
    )
    assert patch.calls.last.request.url.params == httpx.QueryParams({"version": "7"})
    assert json.loads(patch.calls.last.request.content) == {"title": "Archivierter Titel"}
    assert document.owner_id == 17
