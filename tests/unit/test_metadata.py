from __future__ import annotations

from mistraldock.services.metadata import metadata_response_format


def test_metadata_response_format_uses_sdk_schema_with_allowed_tag_enum() -> None:
    response_format = metadata_response_format(["Steuern", "Versicherung"])

    payload = response_format.model_dump(by_alias=True, exclude_none=True)
    schema = payload["json_schema"]["schema"]

    assert payload["type"] == "json_schema"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["title", "created", "tags"]
    assert schema["properties"]["tags"]["items"]["enum"] == ["Steuern", "Versicherung"]
