from __future__ import annotations

import json
from pathlib import Path


def test_native_entrypoints_and_single_env_checker_exist() -> None:
    root = Path(__file__).resolve().parents[2]

    for relative in (
        "run_backend.py",
        "run_worker.py",
        "run_mineru.py",
        "scripts/check_env.py",
        "scripts/run_migrations.py",
    ):
        assert (root / relative).is_file()
    assert not (root / "scripts/check_config.py").exists()
    assert not (root / "scripts/validate_config.py").exists()


def test_frontend_has_npm_dev_script() -> None:
    root = Path(__file__).resolve().parents[2]
    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["dev"]
    assert (root / "frontend/package-lock.json").is_file()
