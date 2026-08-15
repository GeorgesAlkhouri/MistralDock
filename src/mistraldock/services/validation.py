"""Validation before a Paperless document update."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

_GENERIC_TITLES = frozenset(
    {
        "document",
        "dokument",
        "scan",
        "untitled",
        "ohne titel",
        "ihre rechnung",
        "rechnung",
        "brief",
        "schreiben",
    }
)
_MIN_DOCUMENT_DATE = date(1900, 1, 1)


class MetadataValidationError(ValueError):
    """A Mistral result is unsafe to apply to Paperless."""


class DocumentMetadata(BaseModel):
    """The only business metadata Mistral may return."""

    title: str
    created: date | str | None = None
    tags: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ValidatedUpdate:
    """A validated, safe-to-send Paperless PATCH body plus nonfatal warnings."""

    payload: dict[str, object]
    warnings: tuple[str, ...]


def build_validated_update(
    *,
    metadata: DocumentMetadata,
    content: str,
    current_tag_ids: set[int],
    tags_by_name: Mapping[str, int],
    original_filename: str,
    today: date,
) -> ValidatedUpdate:
    """Build the only PATCH body MistralDock is allowed to send."""
    _validate_content(content)
    title = _validate_title(metadata.title, original_filename)
    resolved_tags, warnings = _resolve_tag_ids(metadata.tags, tags_by_name)
    payload: dict[str, object] = {
        "content": content,
        "title": title,
        "tags": sorted(current_tag_ids | resolved_tags),
    }
    created = _validate_created(metadata.created, today)
    if created is not None:
        payload["created"] = created.isoformat()
    elif metadata.created is not None:
        warnings.append("invalid_created")
    return ValidatedUpdate(payload=payload, warnings=tuple(warnings))


def _validate_content(content: str) -> None:
    if not any(character.isalnum() for character in content):
        raise MetadataValidationError("content must contain an alphanumeric character")


def _validate_title(title: str, original_filename: str) -> str:
    if "\n" in title or "\r" in title or any(character.isspace() and character not in " \t" for character in title):
        raise MetadataValidationError("title must not contain line breaks or control whitespace")
    normalized = " ".join(title.split())
    if not 5 <= len(normalized) <= 128:
        raise MetadataValidationError("title must contain between 5 and 128 characters")
    if not any(character.isalnum() for character in normalized):
        raise MetadataValidationError("title must contain an alphanumeric character")
    if normalized.casefold() in _GENERIC_TITLES:
        raise MetadataValidationError("title is a generic placeholder")
    if normalized.casefold() == Path(original_filename).stem.casefold():
        raise MetadataValidationError("title must not equal the original filename")
    return normalized


def _validate_created(value: date | str | None, today: date) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        candidate = value
    else:
        try:
            candidate = date.fromisoformat(value)
        except ValueError:
            return None
    if not _MIN_DOCUMENT_DATE <= candidate <= today + timedelta(days=1):
        return None
    return candidate


def _resolve_tag_ids(tags: list[str], tags_by_name: Mapping[str, int]) -> tuple[set[int], list[str]]:
    resolved: set[int] = set()
    warnings: list[str] = []
    for name in tags:
        tag_id = tags_by_name.get(name)
        if tag_id is None:
            warning = f"unknown_tag:{name}"
            if warning not in warnings:
                warnings.append(warning)
            continue
        resolved.add(tag_id)
    return resolved, warnings
