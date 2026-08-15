"""Runtime configuration loaded from the environment."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WriteMode(StrEnum):
    """Whether validated updates are recorded or sent to Paperless."""

    DRY_RUN = "dry-run"
    LIVE = "live"


class Settings(BaseSettings):
    """Strict MistralDock settings with safe defaults."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    paperless_url: AnyHttpUrl = Field(validation_alias="PAPERLESS_URL")
    paperless_token: SecretStr = Field(validation_alias="PAPERLESS_TOKEN")
    mistral_api_key: SecretStr = Field(validation_alias="MISTRAL_API_KEY")
    mistraldock_api_token: SecretStr = Field(validation_alias="MISTRALDOCK_API_TOKEN")
    paperless_api_version: int = Field(default=10, validation_alias="PAPERLESS_API_VERSION", ge=1)
    mistral_ocr_model: str = Field(
        default="mistral-ocr-latest", validation_alias="MISTRAL_OCR_MODEL", min_length=1
    )
    mistral_metadata_model: str = Field(
        default="mistral-small-latest", validation_alias="MISTRAL_METADATA_MODEL", min_length=1
    )
    database_url: str = Field(
        default="sqlite:////data/mistraldock.db", validation_alias="DATABASE_URL", min_length=1
    )
    write_mode: WriteMode = Field(default=WriteMode.DRY_RUN, validation_alias="WRITE_MODE")
    ocr_chunk_pages: int = Field(default=8, validation_alias="OCR_CHUNK_PAGES", ge=1, le=8)
    worker_concurrency: int = Field(default=1, validation_alias="WORKER_CONCURRENCY", ge=1, le=1)
    max_attempts: int = Field(default=5, validation_alias="MAX_ATTEMPTS", ge=1)
    retry_base_seconds: int = Field(default=30, validation_alias="RETRY_BASE_SECONDS", ge=1)
    retry_max_seconds: int = Field(default=3600, validation_alias="RETRY_MAX_SECONDS", ge=1)
    document_date_min: str = Field(default="1900-01-01", validation_alias="DOCUMENT_DATE_MIN")

    @model_validator(mode="after")
    def tokens_must_differ(self) -> Settings:
        """Prevent accidental reuse of the Paperless credential as API auth."""
        if self.paperless_token.get_secret_value() == self.mistraldock_api_token.get_secret_value():
            raise ValueError("PAPERLESS_TOKEN and MISTRALDOCK_API_TOKEN must differ")
        return self
