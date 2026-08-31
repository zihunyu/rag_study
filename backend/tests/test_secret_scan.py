from __future__ import annotations

from pathlib import Path

from ragkb.engineering_security.secret_scan import scan_repository_for_secrets


def test_current_repository_has_no_static_secret_findings() -> None:
    root = Path(__file__).resolve().parents[2]
    assert scan_repository_for_secrets(root) == []


def test_scanner_reports_file_and_rule_but_not_value(tmp_path: Path) -> None:
    secret_value = "SECRET" + "VALUE" + "12345678901234567890"
    (tmp_path / "bad.py").write_text(f'api_key = "{secret_value}"\n', encoding="utf-8")

    findings = scan_repository_for_secrets(tmp_path)

    assert findings == [{"file": "bad.py", "rule": "credential_assignment"}]
    assert secret_value not in str(findings)
