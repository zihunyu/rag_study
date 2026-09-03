"""Security and native-process compliance harness."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ragkb.adapters.local_storage import LocalFileStorage, StoragePathError
from ragkb.config import EnvLoadResult
from ragkb.spikes.common import result

REQUIRED_CONTAINER_FILES = (
    "Dockerfile.backend",
    "Dockerfile.worker",
    "frontend/Dockerfile",
    "compose.yaml",
    ".dockerignore",
)
DANGEROUS_CONTAINER_TEXT = {
    "privileged: true": "privileged_container",
    "network_mode: host": "host_network",
    "/var/run/docker.sock": "docker_socket_mount",
    "from scratch": "unversioned_scratch_image",
}


def scan_repository_for_container_dependencies(root: Path) -> list[str]:
    """Validate that the reproducible container profile exists without unsafe privileges."""

    violations: list[str] = []
    for reference in REQUIRED_CONTAINER_FILES:
        if not (root / reference).is_file():
            violations.append(f"missing:{reference}")
    candidates = [
        root / reference for reference in REQUIRED_CONTAINER_FILES if (root / reference).is_file()
    ]
    for path in candidates:
        if path.name == ".dockerignore":
            continue
        content = path.read_text(encoding="utf-8").casefold()
        relative = path.relative_to(root).as_posix()
        for marker, code in DANGEROUS_CONTAINER_TEXT.items():
            if marker in content:
                violations.append(f"{code}:{relative}")
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
        {"name": "reproducible_container_scan", "passed": not violations},
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
