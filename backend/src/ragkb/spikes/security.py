"""Security and local-process compliance harness."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ragkb.adapters.local_storage import LocalFileStorage, StoragePathError
from ragkb.config.models import LoadedConfiguration
from ragkb.spikes.common import is_stubbed, result, value_at

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
    relative = path.relative_to(root)
    return any(part in IGNORED_DIRECTORY_NAMES for part in relative.parts)


def _forbidden_filename(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in FORBIDDEN_FILENAMES
        or name.startswith("dockerfile.")
        or ("compose" in name and path.suffix.casefold() in {".yaml", ".yml"})
    )


def scan_repository_for_container_dependencies(root: Path) -> list[str]:
    """Scan all repository filenames and executable/configuration content."""

    violations: list[str] = []
    all_files = [path for path in root.rglob("*") if path.is_file() and not _ignored(path, root)]
    for path in all_files:
        relative = path.relative_to(root).as_posix()
        if _forbidden_filename(path):
            violations.append(f"filename:{relative}")
    content_candidates: list[Path] = [
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.casefold() in CONTENT_SUFFIXES
    ]
    for directory in CONTENT_SCAN_ROOTS:
        candidate_root = root / directory
        if candidate_root.is_dir():
            content_candidates.extend(
                path
                for path in candidate_root.rglob("*")
                if path.is_file() and not _ignored(path, root)
            )
    for path in content_candidates:
        relative = path.relative_to(root).as_posix()
        if relative in CONTENT_SCAN_EXCLUSIONS:
            continue
        if path.suffix.casefold() not in CONTENT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if FORBIDDEN_COMMAND.search(content):
            violations.append(f"content:{relative}")
    return sorted(set(violations))


def run_security_spike(loaded: LoadedConfiguration) -> dict[str, object]:
    traversal_rejected = False
    atomic_roundtrip = False
    with tempfile.TemporaryDirectory(prefix="ragkb-g0-security-") as temporary:
        storage = LocalFileStorage(Path(temporary) / "storage")
        storage.ensure_layout()
        try:
            storage.write_bytes("original", "../escape.bin", b"blocked")
        except StoragePathError:
            traversal_rejected = True
        storage.write_bytes("artifacts", "tenant-a/document-a/result.json", b"{}")
        atomic_roundtrip = (
            storage.read_bytes("artifacts", "tenant-a/document-a/result.json") == b"{}"
        )
    violations = scan_repository_for_container_dependencies(loaded.repository_root)
    secret_names_only = all(
        item.name and isinstance(item.configured, bool) for item in loaded.secret_statuses
    )
    assertions = [
        {"name": "local_storage_path_traversal_rejected", "passed": traversal_rejected},
        {"name": "local_storage_atomic_roundtrip", "passed": atomic_roundtrip},
        {"name": "native_process_implementation_scan", "passed": not violations},
        {"name": "secret_inventory_contains_presence_only", "passed": secret_names_only},
    ]
    blockers = [
        path
        for path in (
            "security_compliance.highest_classification_in_scope",
            "security_compliance.pii_dlp_required",
            "security_compliance.cross_border_transfer_allowed",
            "security_compliance.legal_hold_required",
            "security_compliance.online_content_retention_days",
            "security_compliance.backup_retention_days",
            "security_compliance.audit_retention_days",
            "security_compliance.tenant_offboarding_purge_days",
            "infrastructure.local_storage.encryption_at_rest",
            "infrastructure.local_storage.max_total_size_gb",
        )
        if is_stubbed(loaded.stubbed_paths, path)
    ]
    blockers.extend(
        [
            "threat_model_not_approved",
            "native_process_resource_isolation_not_verified",
            "backup_and_restore_not_exercised",
        ]
    )
    if is_stubbed(loaded.stubbed_paths, "security_compliance.malware_scanner"):
        blockers.append("malware_scanner_not_selected")
    if not value_at(loaded.user, "security_compliance.approved_ai_processing_regions"):
        blockers.append("provider_processing_region_not_approved")
    return result(
        "security_compliance",
        assertions,
        blockers,
        {
            "implementation_violations": violations,
            "secret_variable_name_count": len(loaded.secret_statuses),
            "secret_values_in_report": False,
        },
    )
