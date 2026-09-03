from __future__ import annotations

from pathlib import Path

from ragkb.config import find_repository_root
from ragkb.spikes.security import scan_repository_for_container_dependencies


def test_repository_scan_covers_current_workspace() -> None:
    assert scan_repository_for_container_dependencies(find_repository_root()) == []


def test_repository_scan_checks_all_filenames(tmp_path: Path) -> None:
    for reference in (
        "Dockerfile.backend",
        "Dockerfile.worker",
        "frontend/Dockerfile",
        "compose.yaml",
        ".dockerignore",
    ):
        path = tmp_path / reference
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("FROM python:3.12-slim", encoding="utf-8")
    (tmp_path / "compose.yaml").write_text("privileged: true", encoding="utf-8")

    violations = scan_repository_for_container_dependencies(tmp_path)

    assert violations == ["privileged_container:compose.yaml"]


def test_repository_scan_checks_ci_and_root_script_content(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile.backend").write_text("FROM python:3.12-slim", encoding="utf-8")

    violations = scan_repository_for_container_dependencies(tmp_path)

    assert "missing:compose.yaml" in violations
    assert "missing:Dockerfile.worker" in violations


def test_repository_scan_excludes_historical_plan_content(tmp_path: Path) -> None:
    historical_command = "docker" + " run legacy"
    (tmp_path / "完整开发计划.md").write_text(historical_command, encoding="utf-8")

    violations = scan_repository_for_container_dependencies(tmp_path)
    assert all("完整开发计划" not in item for item in violations)
