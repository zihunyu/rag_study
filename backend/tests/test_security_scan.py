from __future__ import annotations

from pathlib import Path

from ragkb.config import find_repository_root
from ragkb.spikes.security import scan_repository_for_container_dependencies


def test_repository_scan_covers_current_workspace() -> None:
    assert scan_repository_for_container_dependencies(find_repository_root()) == []


def test_repository_scan_checks_all_filenames(tmp_path: Path) -> None:
    forbidden_name = "Docker" + "file"
    nested = tmp_path / "unlisted-directory"
    nested.mkdir()
    (nested / forbidden_name).write_text("FROM scratch", encoding="utf-8")

    violations = scan_repository_for_container_dependencies(tmp_path)

    assert violations == [f"filename:unlisted-directory/{forbidden_name}"]


def test_repository_scan_checks_ci_and_root_script_content(tmp_path: Path) -> None:
    forbidden_command = "docker" + " build ."
    root_script = tmp_path / "run_service.py"
    root_script.write_text(f"# {forbidden_command}\n", encoding="utf-8")
    workflow = tmp_path / ".github/workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text(f"run: {forbidden_command}\n", encoding="utf-8")

    violations = scan_repository_for_container_dependencies(tmp_path)

    assert violations == ["content:.github/workflows/ci.yml", "content:run_service.py"]


def test_repository_scan_excludes_historical_plan_content(tmp_path: Path) -> None:
    historical_command = "docker" + " run legacy"
    (tmp_path / "完整开发计划.md").write_text(historical_command, encoding="utf-8")

    assert scan_repository_for_container_dependencies(tmp_path) == []
