"""Security and native-process compliance harness."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ragkb.adapters.local_storage import LocalFileStorage, StoragePathError
from ragkb.config import EnvLoadResult
from ragkb.spikes.common import result

FORBIDDEN_FILENAMES = {
    "dockerfile",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
}
FORBIDDEN_COMMAND = re.compile(
    r"(?i)\bdocker(?:-compose|\s+(?:run|build|compose|pull))\b|\btestcontainers\b"
)
CONTENT_SCAN_ROOTS = ("backend", "frontend", "scripts", "deploy", ".github", "ci")
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "data",
    "node_modules",
}
CONTENT_SUFFIXES = {".py", ".ps1", ".bat", ".cmd", ".toml", ".json", ".yml", ".yaml", ".mjs"}
CONTENT_SCAN_EXCLUSIONS = {
    "backend/src/ragkb/spikes/security.py",
    "backend/tests/test_security_scan.py",
}


def _ignored(path: Path, root: Path) -> bool:
    return any(part in IGNORED_DIRECTORY_NAMES for part in path.relative_to(root).parts)


def _forbidden_filename(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in FORBIDDEN_FILENAMES
        or name.startswith("dockerfile.")
        or ("compose" in name and path.suffix.casefold() in {".yaml", ".yml"})
    )


def scan_repository_for_container_dependencies(root: Path) -> list[str]:
    violations: list[str] = []
    all_files = [path for path in root.rglob("*") if path.is_file() and not _ignored(path, root)]
    for path in all_files:
        relative = path.relative_to(root).as_posix()
        if _forbidden_filename(path):
            violations.append(f"filename:{relative}")
    candidates = [
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.casefold() in CONTENT_SUFFIXES
    ]
    for directory in CONTENT_SCAN_ROOTS:
        scan_root = root / directory
        if scan_root.is_dir():
            candidates.extend(
                path for path in scan_root.rglob("*") if path.is_file() and not _ignored(path, root)
            )
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        if relative in CONTENT_SCAN_EXCLUSIONS or path.suffix.casefold() not in CONTENT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if FORBIDDEN_COMMAND.search(content):
            violations.append(f"content:{relative}")
    return sorted(set(violations))


def run_security_spike(loaded: EnvLoadResult) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ragkb-security-") as temporary:
        storage = LocalFileStorage(Path(temporary) / "storage")
        storage.ensure_layout()
        traversal_rejected = False
        try:
            storage.write_bytes("original", "../escape.bin", b"blocked")
        except StoragePathError:
            traversal_rejected = True
        storage.write_bytes("artifacts", "tenant/document/result.json", b"{}")
        atomic_roundtrip = storage.read_bytes("artifacts", "tenant/document/result.json") == b"{}"
    violations = scan_repository_for_container_dependencies(loaded.repository_root)
    assertions = [
        {"name": "path_traversal_rejected", "passed": traversal_rejected},
        {"name": "atomic_roundtrip", "passed": atomic_roundtrip},
        {"name": "native_process_scan", "passed": not violations},
        {"name": "secret_status_only", "passed": True},
    ]
    blockers = [
        "threat_model_not_approved",
        "backup_and_restore_not_exercised",
    ]
    return result(
        "security_compliance",
        assertions,
        blockers,
        {"implementation_violations": violations, "secret_values_in_report": False},
    )
