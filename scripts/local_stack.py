"""Ownership-safe native local stack helper; never uses containers."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from process_supervisor import (
    LocalProcessSupervisor,
    ProcessSpec,
    SubprocessLauncher,
    SystemProcessInspector,
)

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/storage/temp/local-stack.json"


def _plan() -> dict[str, object]:
    return {
        "backend": [sys.executable, "run_backend.py"],
        "worker": [sys.executable, "run_worker.py"],
        "frontend": ["npm", "run", "dev"],
        "frontend_cwd": "frontend",
        "ownership_fields": [
            "pid",
            "create_time",
            "executable",
            "command",
            "cwd",
            "owner_token",
        ],
        "docker": False,
        "graceful_signal": "SIGTERM",
        "simulated": True,
        "real_acceptance": False,
    }


def _supervisor() -> tuple[LocalProcessSupervisor, list[ProcessSpec]]:
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm executable was not found")
    inspector = SystemProcessInspector()
    supervisor = LocalProcessSupervisor(
        STATE,
        inspector,
        SubprocessLauncher(inspector, ROOT / "scripts/owned_process.py"),
    )
    specs = [
        ProcessSpec("backend", (sys.executable, "run_backend.py"), ROOT),
        ProcessSpec("worker", (sys.executable, "run_worker.py"), ROOT),
        ProcessSpec("frontend", (npm, "run", "dev"), ROOT / "frontend"),
    ]
    return supervisor, specs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "status", "start", "stop"))
    args = parser.parse_args()
    if args.action == "plan":
        print(json.dumps(_plan(), ensure_ascii=False, sort_keys=True))
        return 0
    supervisor, specs = _supervisor()
    if args.action == "status":
        print(json.dumps(supervisor.status(), sort_keys=True))
        return 0
    if args.action == "start":
        identities = supervisor.start(specs)
        print(json.dumps({"started": [item.name for item in identities], "docker": False}))
        return 0
    result = supervisor.stop()
    print(json.dumps(result, sort_keys=True))
    return 2 if result["refused"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
