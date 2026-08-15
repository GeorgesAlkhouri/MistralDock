"""Packaging invariants required by the production container."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_runtime_dependencies_include_the_migration_runner() -> None:
    project_file = Path(__file__).parents[2] / "pyproject.toml"

    project = tomllib.loads(project_file.read_text())

    assert any(dependency.startswith("alembic") for dependency in project["project"]["dependencies"])
