"""Paperless-ngx REST API v9 client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Self
from urllib.parse import urljoin, urlsplit

import anyio
import httpx


class PaperlessProtocolError(ValueError):
    """Paperless responded successfully but without data required by MistralDock."""


def _parse_tag(tag: object) -> tuple[int, str, int | None]:
    if not isinstance(tag, dict):
        raise PaperlessProtocolError("invalid_tag")
    tag_id = tag.get("id")
    name = tag.get("name")
    if "owner" not in tag:
        raise PaperlessProtocolError("invalid_tag")
    owner_id = tag["owner"]
    if (
        not isinstance(tag_id, int)
        or not isinstance(name, str)
        or (owner_id is not None and type(owner_id) is not int)
    ):
        raise PaperlessProtocolError("invalid_tag")
    return tag_id, name, owner_id


def _merge_tags(results: list[object], tags_by_name: dict[str, int], owner_id: int) -> None:
    for tag in results:
        tag_id, name, tag_owner_id = _parse_tag(tag)
        if tag_owner_id != owner_id:
            continue
        if name in tags_by_name and tags_by_name[name] != tag_id:
            raise PaperlessProtocolError("duplicate_tag_name")
        tags_by_name[name] = tag_id


def _next_tag_url(base_url: str, next_value: object) -> str | None:
    if next_value is not None and not isinstance(next_value, str):
        raise PaperlessProtocolError("invalid_tag_next")
    if not next_value:
        return None
    next_url = urljoin(base_url, next_value)
    if _origin(next_url) != _origin(base_url):
        raise PaperlessProtocolError("invalid_tag_next_origin")
    return next_url


@dataclass(frozen=True)
class PaperlessDocument:
    document_id: int
    tags: frozenset[int]
    owner_id: int | None
    modified: str
    original_filename: str
    version_id: int


class PaperlessClient:
    """Make only the Paperless requests permitted by the MistralDock design."""

    def __init__(
        self, base_url: str, token: str, *, api_version: int, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        headers = {
            "Accept": f"application/json; version={api_version}",
            "Authorization": f"Token {token}",
        }
        self._client = client or httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(300.0))
        self._client.headers.update(headers)
        self._owns_client = client is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_document(self, document_id: int) -> PaperlessDocument:
        response = await self._client.get(self._url(f"api/documents/{document_id}/"))
        response.raise_for_status()
        data = response.json()
        versions = data.get("versions")
        if not isinstance(versions, list) or not versions or not isinstance(versions[-1], dict):
            raise PaperlessProtocolError("missing_document_version")
        version_id = versions[-1].get("id")
        if not isinstance(version_id, int):
            raise PaperlessProtocolError("invalid_document_version")
        tags = data.get("tags")
        if not isinstance(tags, list) or not all(isinstance(tag, int) for tag in tags):
            raise PaperlessProtocolError("invalid_document_tags")
        if "owner" not in data:
            raise PaperlessProtocolError("invalid_document_owner")
        owner_id = data["owner"]
        if owner_id is not None and type(owner_id) is not int:
            raise PaperlessProtocolError("invalid_document_owner")
        for field in ("modified", "original_file_name"):
            if not isinstance(data.get(field), str):
                raise PaperlessProtocolError(f"missing_{field}")
        return PaperlessDocument(
            document_id=document_id,
            tags=frozenset(tags),
            owner_id=owner_id,
            modified=data["modified"],
            original_filename=data["original_file_name"],
            version_id=version_id,
        )

    async def list_tags(self, owner_id: int) -> dict[str, int]:
        tags_by_name: dict[str, int] = {}
        next_url: str | None = self._url("api/tags/")
        while next_url is not None:
            response = await self._client.get(next_url)
            response.raise_for_status()
            data = response.json()
            results = data.get("results")
            if not isinstance(results, list):
                raise PaperlessProtocolError("invalid_tag_page")
            _merge_tags(results, tags_by_name, owner_id)
            next_url = _next_tag_url(self._base_url, data.get("next"))
        return tags_by_name

    async def download_original(self, document: PaperlessDocument, destination: Path) -> None:
        url = self._url(f"api/documents/{document.document_id}/download/")
        async with self._client.stream(
            "GET", url, params={"original": "true", "version": document.version_id}
        ) as response:
            response.raise_for_status()
            async with await anyio.open_file(destination, "wb") as handle:
                async for chunk in response.aiter_bytes():
                    await handle.write(chunk)

    async def patch_document(self, document: PaperlessDocument, payload: dict[str, object]) -> None:
        response = await self._client.patch(
            self._url(f"api/documents/{document.document_id}/"),
            params={"version": document.version_id},
            json=payload,
        )
        response.raise_for_status()

    def _url(self, path: str) -> str:
        return urljoin(self._base_url, path)


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    port = parsed.port
    if port is None:
        port = {"http": 80, "https": 443}.get(parsed.scheme.lower())
    return parsed.scheme.lower(), parsed.hostname, port
