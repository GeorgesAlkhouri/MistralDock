from __future__ import annotations

from pathlib import Path

import yaml


def test_example_compose_has_no_paperless_volume_mount() -> None:
    compose = yaml.safe_load(Path("compose.example.yml").read_text())

    assert compose["services"]["mistraldock"].get("volumes") == ["mistraldock-data:/data"]
    assert compose["services"]["mistraldock"]["read_only"] is True


def test_env_example_defaults_to_dry_run() -> None:
    environment = Path(".env.example").read_text()

    assert "WRITE_MODE=dry-run" in environment
    assert "PAPERLESS_TOKEN=" in environment
    assert "MISTRAL_API_KEY=" in environment
