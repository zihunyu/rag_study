"""Export parsed UAT retest inputs for user-only local verification."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import load_env  # noqa: E402
from ragkb.evaluation.uat_error_case_retest import select_retest_case_ids  # noqa: E402

REVIEW = ROOT / "artifacts/user-review/uat-v4-package-20260902/UAT_v4_逐项审核结果.jsonl"
SOURCE_PLAN = ROOT / "artifacts/final-validation/uat-systematic-revision-v5-plan.json"
PREFLIGHT = ROOT / "data/storage/artifacts/uat-future-error-retest-v4/preflight-blocked.json"
OUTPUT = ROOT / "artifacts/user-review/uat-future-error-retest-parsed-content-review.zip"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes(), usedforsecurity=False).hexdigest()


def _payload(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def main() -> int:
    rows = [json.loads(line) for line in REVIEW.read_text(encoding="utf-8").splitlines() if line]
    selected_ids = select_retest_case_ids([row for row in rows if isinstance(row, Mapping)])
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    plan = json.loads(SOURCE_PLAN.read_text(encoding="utf-8"))
    records = {str(record["candidate_id"]): record for record in plan["selected_bundles"]}
    blocked = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    blocked_by_hash = {
        str(item.get("selected_case_sha256")): item for item in blocked if isinstance(item, Mapping)
    }
    entries: dict[str, bytes] = {}
    summary = []
    for index, selected_id in enumerate(selected_ids, start=1):
        record = records.get(selected_id)
        if not isinstance(record, Mapping):
            raise RuntimeError("UAT_REVIEW_EXPORT_CASE_MISSING")
        path = (artifacts_root / str(record["bundle_ref"])).resolve()
        if artifacts_root not in path.parents or _sha256(path) != record.get("bundle_sha256"):
            raise RuntimeError("UAT_REVIEW_EXPORT_BUNDLE_HASH_INVALID")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        case = {
            "review_index": index,
            "question": bundle.get("question"),
            "source_category": bundle.get("source_category"),
            "expected_locator": bundle.get("expected_locator"),
            "documents": bundle.get("documents"),
            "source_bundle_sha256": record.get("bundle_sha256"),
            "render_proof_preflight": blocked_by_hash.get(
                hashlib.sha256(selected_id.encode(), usedforsecurity=False).hexdigest()
            ),
            "old_model_answer_included": False,
        }
        name = f"cases/{index:02d}.json"
        entries[name] = _payload(case)
        summary.append(
            {
                "review_index": index,
                "case_file": name,
                "source_category": case["source_category"],
                "preflight_state": case["render_proof_preflight"].get("state")
                if isinstance(case["render_proof_preflight"], Mapping)
                else "NOT_EVALUATED",
            }
        )
    entries["README.md"] = (
        b"# Parsed content review package\n\n"
        b"Each case contains the original UAT question, parsed evidence documents, locators, "
        b"and the local render-proof preflight result. Historical model answers are "
        b"deliberately excluded.\n"
    )
    entries["manifest.json"] = _payload(
        {
            "revision": "uat-parsed-content-review-package:v1",
            "selected_case_count": len(selected_ids),
            "case_summary": summary,
            "review_input_sha256": _sha256(REVIEW),
            "source_plan_sha256": _sha256(SOURCE_PLAN),
            "old_model_answers_included": False,
            "provider_call_count": 0,
        }
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(name, content)
    print(
        json.dumps(
            {"output": str(OUTPUT), "case_count": len(selected_ids), "provider_call_count": 0}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
