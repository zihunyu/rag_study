"""Static repository secret scan that never reports matched values."""

from __future__ import annotations

import re
from pathlib import Path

IGNORED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    "data",
    "node_modules",
}
EXCLUDED_FILES = {
    "config/.env",
    "config/.env.example",
    "backend/src/ragkb/engineering_security/secret_scan.py",
    "backend/tests/test_env_config.py",
    "backend/tests/test_secret_scan.py",
    "backend/tests/test_zilliz_adapter.py",
}
TEXT_SUFFIXES = {
    ".py",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".txt",
    ".mjs",
    ".html",
    ".ps1",
    ".cmd",
    ".bat",
}
RULES = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(
        r"(?i)(?:api[_-]?key|token|password|secret)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{20,}"
    ),
    "common_secret_prefix": re.compile(r"(?:sk|rk|pk)_[A-Za-z0-9]{24,}"),
}


def scan_repository_for_secrets(root: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        relative_text = relative.as_posix()
        if relative_text in EXCLUDED_FILES or any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for rule, pattern in RULES.items():
            if pattern.search(content):
                findings.append({"file": relative_text, "rule": rule})
    return sorted(findings, key=lambda item: (item["file"], item["rule"]))
