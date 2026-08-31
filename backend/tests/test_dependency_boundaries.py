from __future__ import annotations

import ast
from pathlib import Path


def _imports(root: Path) -> set[str]:
    modules: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
    return modules


def test_domain_has_no_framework_database_or_supplier_sdk_imports() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src/ragkb/domain"
    imports = _imports(source_root)
    forbidden = {
        "fastapi",
        "sqlalchemy",
        "sqlite3",
        "openpyxl",
        "pypdf",
        "docx",
        "pptx",
        "pymilvus",
        "xlrd",
        "uvicorn",
    }

    assert not any(module.split(".")[0] in forbidden for module in imports)


def test_application_does_not_import_infrastructure_api_or_supplier_sdks() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src/ragkb/application"
    imports = _imports(source_root)
    forbidden_prefixes = (
        "ragkb.adapters",
        "ragkb.api",
        "ragkb.infrastructure",
        "ragkb.document_processing",
        "fastapi",
        "sqlite3",
        "openpyxl",
        "pypdf",
        "docx",
        "pptx",
        "pymilvus",
        "xlrd",
    )

    assert not any(module.startswith(forbidden_prefixes) for module in imports)
