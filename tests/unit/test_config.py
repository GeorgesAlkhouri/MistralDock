from __future__ import annotations

import pytest
from pydantic import ValidationError

from mistraldock.config import Settings, WriteMode


@pytest.fixture
def valid_env() -> dict[str, str]:
    return {
        "PAPERLESS_URL": "https://paperless.example",
        "PAPERLESS_TOKEN": "paperless-token",
        "MISTRAL_API_KEY": "mistral-key",
        "MISTRALDOCK_API_TOKEN": "service-token",
    }


def test_settings_default_to_dry_run(valid_env: dict[str, str]) -> None:
    settings = Settings(_env_file=None, **valid_env)

    assert settings.write_mode is WriteMode.DRY_RUN
    assert settings.ocr_chunk_pages == 8
    assert settings.paperless_api_version == 9


def test_settings_reject_identical_paperless_and_service_tokens(
    valid_env: dict[str, str],
) -> None:
    valid_env["MISTRALDOCK_API_TOKEN"] = valid_env["PAPERLESS_TOKEN"]

    with pytest.raises(ValidationError, match="must differ"):
        Settings(_env_file=None, **valid_env)
