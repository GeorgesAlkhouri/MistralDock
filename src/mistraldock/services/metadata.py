"""Prompts and JSON schemas for the three allowed metadata fields."""

from __future__ import annotations

from typing import Any


def metadata_response_format(vocabulary: list[str]) -> dict[str, Any]:
    """Build Mistral's strict JSON response format from current Paperless tags."""
    tag_item: dict[str, object] = {"type": "string"}
    if vocabulary:
        tag_item["enum"] = vocabulary
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "paperless_document_metadata",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "created", "tags"],
                "properties": {
                    "title": {"type": "string", "minLength": 5, "maxLength": 128},
                    "created": {"type": ["string", "null"], "format": "date"},
                    "tags": {"type": "array", "uniqueItems": True, "items": tag_item},
                },
            },
        },
    }


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
