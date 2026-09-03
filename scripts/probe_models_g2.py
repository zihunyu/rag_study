"""Execute the explicitly approved bounded G2 model probes exactly once each."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.model_http import (  # noqa: E402
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
)
from ragkb.application.model_probe import run_bounded_model_probes  # noqa: E402
from ragkb.config import build_env_report, load_env  # noqa: E402

APPROVAL = "BILLABLE_MODEL_CALL_APPROVED"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded approved G2 model probes")
    parser.add_argument("--approval", required=True)
    parser.add_argument("--embedding-limit", type=int, default=5)
    parser.add_argument("--reranker-limit", type=int, default=5)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/g2/model-probe-evidence.json")
    )
    args = parser.parse_args()
    if args.approval != APPROVAL:
        print("status=BILLABLE_MODEL_CALL_APPROVAL_REQUIRED")
        return 2
    loaded = load_env(ROOT)
    gate = build_env_report(loaded, "G2")
    if loaded.settings is None or not gate["summary"]["gate_ready"]:  # type: ignore[index]
        print(json.dumps({"status": "G2_CONFIG_NOT_READY", "gate_blockers": gate["gate_blockers"]}))
        return 3
    evidence = run_bounded_model_probes(
        OpenAICompatibleEmbeddingAdapter(loaded.settings, external_call_approved=True),
        OpenAICompatibleRerankerAdapter(loaded.settings, external_call_approved=True),
        embedding_limit=args.embedding_limit,
        reranker_limit=args.reranker_limit,
    )
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if evidence["status"] == "MODEL_PROBES_PASSED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
