"""Build protected fresh-evidence cases for eligible future UAT retests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("preflight", type=Path)
    p.add_argument("confirmation_root", type=Path)
    p.add_argument("--output-root", type=Path, required=True)
    a = p.parse_args()
    r = json.loads(a.preflight.read_text())
    eligible = [x for x in r["records"] if x["state"] == "ELIGIBLE"]
    a.output_root.mkdir(parents=True, exist_ok=True)
    mapping = (
        json.loads((a.confirmation_root / "input_cases" / "01.json").read_text(encoding="utf-8"))
        if eligible
        else {}
    )
    for item in eligible:
        case = {
            "test_case_id": item["test_case_id"],
            "question": mapping.get("question"),
            "evidence": [
                {
                    "content": mapping.get("documents", []),
                    "locator": mapping.get("expected_locator"),
                    "source_classification": item.get("source_classification"),
                    "source_sha256": item.get("source_sha256"),
                    "representation_sha256": item.get("representation_sha256"),
                }
            ],
            "old_model_answer_included": False,
        }
        (a.output_root / f"{item['test_case_id']}.json").write_text(json.dumps(case) + "\n")
    print(
        json.dumps(
            {"eligible_count": len(eligible), "provider_call_count": 0, "content_output": False}
        )
    )


if __name__ == "__main__":
    main()
