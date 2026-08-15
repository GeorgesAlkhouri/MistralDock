"""Prompts and JSON schemas for the three allowed metadata fields."""

from __future__ import annotations

from typing import Any, Literal

from mistralai.extra import response_format_from_pydantic_model
from pydantic import ConfigDict, create_model


def metadata_response_format(vocabulary: list[str]) -> Any:
    """Build an SDK-compatible strict schema from current Paperless tags."""
    tag_type = str if not vocabulary else Literal.__getitem__(tuple(vocabulary))
    metadata_model = create_model(
        "PaperlessDocumentMetadata",
        __config__=ConfigDict(extra="forbid"),
        title=(str, ...),
        created=(str | None, ...),
        tags=(list[tag_type], ...),
    )
    return response_format_from_pydantic_model(metadata_model)


def annotation_prompt(vocabulary: list[str]) -> str:
    return "\n".join(
        [
            "Extrahiere ausschließlich title, created und tags als JSON nach dem vorgegebenen Schema.",
            "title: kurzer archivtauglicher Titel aus dem Inhalt; nutze Absender/Organisation, Dokumentart und Zeitraum/Gegenstand; übernimm keine generische Überschrift wie 'Ihre Rechnung'.",
            "created: fachliches Ausstellungs-, Rechnungs-, Vertrags- oder vergleichbares Dokumentdatum als YYYY-MM-DD; nie Scan-, Upload- oder Verarbeitungsdatum; bei Unsicherheit null.",
            "tags: null, ein oder mehrere fachlich passende Namen ausschließlich aus der erlaubten Liste; erfinde keine Namen.",
            f"Erlaubte Paperless-Tags: {vocabulary!r}",
        ]
    )


def consolidation_prompt(vocabulary: list[str]) -> str:
    return "\n".join(
        [
            "Konsolidiere die geordneten Metadatenkandidaten von Teilen eines einzelnen Dokuments.",
            "Gib ausschließlich title, created und tags im vorgegebenen JSON-Schema zurück.",
            "Wähle einen dokumentweiten archivtauglichen Titel, ein fachliches Datum oder null, und nur passende Tags aus der erlaubten Liste.",
            f"Erlaubte Paperless-Tags: {vocabulary!r}",
        ]
    )
