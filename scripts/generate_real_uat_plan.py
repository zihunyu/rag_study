"""Generate deterministic local UAT bundles and a content-free execution plan."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.real_uat import build_uat_bundles  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = build_uat_bundles(ROOT)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    categories = Counter(str(item["source_category"]) for item in plan["bundles"])
    print(
        json.dumps(
            {
                "revision": plan["revision"],
                "bundle_count": plan["bundle_count"],
                "category_counts": dict(sorted(categories.items())),
                "documents_per_bundle": plan["documents_per_bundle"],
                "reranker_max_requests": plan["reranker"]["max_requests"],
                "llm_max_requests": plan["llm"]["max_requests"],
                "total_model_request_budget": plan["total_model_request_budget"],
                "conditional_user_authorization_satisfied": True,
                "executed": False,
                "query_embedding_request_count": 0,
                "zilliz_request_count": 0,
                "content_output": False,
                "source_names_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
