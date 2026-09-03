"""Run the G3 frozen synthetic evaluation Harness without real model calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.g3_eval import load_g3_eval_dataset, run_g3_eval_harness  # noqa: E402


def main() -> int:
    dataset = load_g3_eval_dataset(
        ROOT / "backend/tests/fixtures/manifests/g3-eval-dataset.yaml",
        ROOT / "backend/src/ragkb/contracts/schemas/g3-eval-dataset-v1.schema.json",
    )
    report = run_g3_eval_harness(dataset, lambda case: str(case["expected_status"]))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["case_count"] == report["passed_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
