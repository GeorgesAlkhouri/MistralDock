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


def test_ghcr_workflow_builds_prs_and_publishes_main() -> None:
    workflow = yaml.load(
        Path(".github/workflows/container-image.yml").read_text(), Loader=yaml.BaseLoader
    )

    assert workflow["on"]["push"]["branches"] == ["main"]
    assert workflow["on"]["pull_request"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "read", "packages": "write"}

    steps = workflow["jobs"]["container"]["steps"]
    login = next(step for step in steps if step.get("uses") == "docker/login-action@v3")
    metadata = next(step for step in steps if step.get("id") == "meta")
    build = next(step for step in steps if step.get("uses") == "docker/build-push-action@v6")

    assert login["if"] == "github.event_name == 'push'"
    assert login["with"]["registry"] == "ghcr.io"
    assert metadata["with"]["images"] == "ghcr.io/${{ github.repository_owner }}/mistraldock"
    assert "type=raw,value=latest" in metadata["with"]["tags"]
    assert "type=sha,prefix=sha-" in metadata["with"]["tags"]
    assert build["with"]["push"] == "${{ github.event_name == 'push' }}"
