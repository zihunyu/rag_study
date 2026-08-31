from __future__ import annotations

import json

import yaml
from ragkb.config.loader import find_repository_root


def test_declared_startup_commands_are_native_processes() -> None:
    root = find_repository_root()
    config = yaml.safe_load(
        (root / "config/user-input/project-inputs.yaml").read_text(encoding="utf-8")
    )

    assert config["deployment"]["target_platform"] == "native_processes"
    assert config["deployment"]["docker_forbidden"] is True
    assert config["deployment"]["startup_commands"] == {
        "backend": "python run_backend.py",
        "worker": "python run_worker.py",
        "mineru": "python run_mineru.py",
        "frontend": "npm run dev",
        "config_check": "python scripts/check_config.py",
    }
    assert (root / "run_backend.py").is_file()
    assert (root / "run_worker.py").is_file()
    assert (root / "run_mineru.py").is_file()
    assert (root / "scripts/run_migrations.py").is_file()


def test_frontend_has_npm_dev_script() -> None:
    root = find_repository_root()
    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["dev"]
    assert (root / "frontend/package-lock.json").is_file()
