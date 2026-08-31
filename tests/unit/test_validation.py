from __future__ import annotations

from datetime import date

import pytest

from mistraldock.services.validation import (
    DocumentMetadata,
    MetadataValidationError,
    build_validated_update,
)


def test_validated_update_preserves_current_tags_and_discards_unknown_suggestions() -> None:
    update = build_validated_update(
        metadata=DocumentMetadata(
            title="Vodafone – Kabelrechnung Juli 2026",
            created=date(2026, 7, 1),
            tags=["Telekommunikation", "Unbekannt"],
        ),
        content="Kabelrechnung für Juli 2026",
        current_tag_ids={4},
        tags_by_name={"Telekommunikation": 9},
        original_filename="scan-2026-07.pdf",
        today=date(2026, 8, 15),
    )

    assert update.payload == {
        "content": "Kabelrechnung für Juli 2026",
        "title": "Vodafone – Kabelrechnung Juli 2026",
        "created": "2026-07-01",
        "tags": [4, 9],
    }
    assert update.warnings == ("unknown_tag:Unbekannt",)


def test_validated_update_omits_uncertain_date() -> None:
    update = build_validated_update(
        metadata=DocumentMetadata(title="Stadtwerke – Jahresabrechnung", created=None, tags=[]),
        content="Abrechnung",
        current_tag_ids=set(),
        tags_by_name={},
        original_filename="rechnung.pdf",
        today=date(2026, 8, 15),
    )

    assert "created" not in update.payload


def test_validated_update_rejects_generic_title() -> None:
    metadata = DocumentMetadata(title="Rechnung", created=None, tags=[])
    today = date(2026, 8, 15)

    with pytest.raises(MetadataValidationError, match="title"):
        build_validated_update(
            metadata=metadata,
            content="Abrechnung",
            current_tag_ids=set(),
            tags_by_name={},
            original_filename="scan.pdf",
            today=today,
        )
