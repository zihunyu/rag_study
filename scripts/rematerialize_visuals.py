"""Back up and deterministically rebuild reviewed diagrams into a NEW DRAFT version.

Default is dry-run. Apply a saved plan with --apply --plan <diagnostic.json> so retries
reuse the same optimistic condition/idempotency key even after a draft has been created.
The script never reviews/publishes or changes active-version pointers. Visual facts are
reused without vision calls; the queued ingestion still performs normal embedding/indexing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP = ROOT / "artifacts/reviews/20260908-visual-rag-fix/rematerialization"


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def save_immutable(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    checksum = hashlib.sha256(content).hexdigest()
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError(f"BACKUP_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT: {path}")
    else:
        with path.open("xb") as output:
            output.write(content)
    return checksum


def backup_plan(client: httpx.Client, report: dict[str, Any], root: Path) -> tuple[Path, str]:
    version = report["source_version_id"]
    folder = root / report["document_id"] / version / report["input_fingerprint"]
    # IDs come from the authenticated management API; still prevent path escape.
    folder = folder.resolve()
    if not folder.is_relative_to(root.resolve()):
        raise ValueError("BACKUP_PATH_OUTSIDE_REQUESTED_DIRECTORY")
    files = {"diagnostic.json": save_immutable(folder / "diagnostic.json", encoded(report))}
    expected = report["backup"]["original_sha256"]
    original = client.get(f"/api/document-versions/{version}/original/preview")
    original.raise_for_status()
    if hashlib.sha256(original.content).hexdigest() != expected:
        raise ValueError("ORIGINAL_CHANGED_AFTER_DRY_RUN")
    files["original.bin"] = save_immutable(folder / "original.bin", original.content)
    for asset in report["backup"]["assets"]:
        response = client.get(f"/api/document-versions/{version}/visuals/{asset['id']}/image")
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest() != asset["sha256"]:
            raise ValueError("IMAGE_CHANGED_AFTER_DRY_RUN")
        name = f"images/{asset['id']}.image"
        target = (folder / name).resolve()
        if not target.is_relative_to(folder):
            raise ValueError("BACKUP_IMAGE_PATH_INVALID")
        files[name] = save_immutable(target, response.content)
    receipt = {
        "input_fingerprint": report["input_fingerprint"],
        "files": files,
        "source_version_id": version,
        "document_id": report["document_id"],
    }
    checksum = save_immutable(folder / "receipt.json", encoded(receipt))
    return folder, checksum


def run(args: argparse.Namespace, client: httpx.Client) -> dict[str, Any]:
    if args.plan:
        report = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    else:
        response = client.get(f"/api/document-versions/{args.version_id}/visual-rematerialization")
        response.raise_for_status()
        report = response.json()
    if report["source_version_id"] != args.version_id or (
        args.document_id and report["document_id"] != args.document_id
    ):
        raise ValueError("PLAN_DOCUMENT_VERSION_MISMATCH")
    folder, receipt = backup_plan(client, report, Path(args.backup_dir))
    result: dict[str, Any] = {
        "mode": "dry-run",
        "backup_directory": str(folder),
        "plan": str(folder / "diagnostic.json"),
        "input_fingerprint": report["input_fingerprint"],
        "source_version_id": args.version_id,
        "vision_model_calls": 0,
        "embedding_reindex_required": True,
        "next_step": (
            "检查 diagnostic.json 的 preview 后，使用 --apply --plan <该文件> 创建草稿；"
            "通过正常发布流程使其生效。"
        ),
    }
    if args.apply:
        command = {
            "url": f"/api/document-versions/{args.version_id}:rematerialize-visuals",
            "headers": {
                "If-Match": str(report["row_version"]),
                "Idempotency-Key": "rematerialize:"
                + args.version_id
                + ":"
                + report["input_fingerprint"],
            },
            "body": {
                "input_fingerprint": report["input_fingerprint"],
                "backup_receipt_sha256": receipt,
            },
        }
        save_immutable(folder / "submission.json", encoded(command))
        response = client.post(command["url"], headers=command["headers"], json=command["body"])
        response.raise_for_status()
        created = response.json()
        save_immutable(folder / "result.json", encoded(created))
        result.update(mode="created-draft", result=created)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--document-id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP))
    parser.add_argument("--plan", help="Use an immutable diagnostic.json from a prior dry-run.")
    parser.add_argument(
        "--apply", action="store_true", help="Create a new draft only after backup."
    )
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        print(json.dumps(run(args, client), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
