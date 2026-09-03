"""Compare the local Jieba analyzer with the configured Milvus field analyzer."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.retrieval_memory import analyze_terms  # noqa: E402
from ragkb.adapters.zilliz import ZillizCloudAdapter  # noqa: E402
from ragkb.config import load_env  # noqa: E402

APPROVAL = "ANALYZER_PARITY_CALL_APPROVED"
PUBLIC_SAMPLES = ("南京市长江大桥", "ThinkPad P16 Gen 3 21FA")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approval", required=True)
    args = parser.parse_args()
    if args.approval != APPROVAL:
        raise SystemExit("ANALYZER_PARITY_CALL_APPROVAL_REQUIRED")
    settings = load_env(ROOT).settings
    if settings is None:
        raise SystemExit("CONFIG_INVALID")
    client = ZillizCloudAdapter(settings).connect()
    results = client.run_analyzer(
        list(PUBLIC_SAMPLES),
        collection_name=settings.zilliz_cloud_collection,
        field_name="retrieval_text",
    )
    remote = [tuple(map(str, result.tokens)) for result in results]
    local = [analyze_terms(text) for text in PUBLIC_SAMPLES]
    matches = [left == right for left, right in zip(local, remote, strict=True)]
    evidence = {
        "status": "ANALYZER_PARITY_PASSED" if all(matches) else "ANALYZER_PARITY_FAILED",
        "sample_count": len(PUBLIC_SAMPLES),
        "matches": matches,
        "local_revision": "milvus-chinese-jieba-search-cnalphanumonly:v1",
        "sample_set_sha256": hashlib.sha256("\n".join(PUBLIC_SAMPLES).encode()).hexdigest(),
        "raw_tokens_in_output": False,
    }
    print(json.dumps(evidence, sort_keys=True))
    return 0 if all(matches) else 1


if __name__ == "__main__":
    raise SystemExit(main())
