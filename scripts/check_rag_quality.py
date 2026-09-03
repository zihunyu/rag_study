"""Validate the gold dataset and fail when retrieval or generation metrics regress."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.retrieval_memory import LocalHybridIndex, LocalIndexRecord  # noqa: E402
from ragkb.adapters.stubs import DeterministicEmbedding  # noqa: E402
from ragkb.application.search import classify_query, rrf_fuse  # noqa: E402
from ragkb.domain.retrieval import SearchContext  # noqa: E402
from ragkb.evaluation.rag_quality import evaluate_quality  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", default="backend/tests/fixtures/manifests/rag-quality-gold.yaml"
    )
    parser.add_argument(
        "--schema", default="backend/src/ragkb/contracts/schemas/rag-quality-dataset-v1.schema.json"
    )
    parser.add_argument(
        "--results", default="backend/tests/fixtures/manifests/rag-quality-results.json"
    )
    parser.add_argument(
        "--corpus", default="backend/tests/fixtures/manifests/rag-quality-corpus.yaml"
    )
    parser.add_argument("--thresholds", default="config/rag-quality-thresholds.json")
    parser.add_argument("--output", default="artifacts/quality/rag-quality-report.json")
    parser.add_argument("--k", type=int, default=1)
    args = parser.parse_args()
    dataset: Any = yaml.safe_load((ROOT / args.dataset).read_text(encoding="utf-8"))
    schema = json.loads((ROOT / args.schema).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(dataset)
    results = json.loads((ROOT / args.results).read_text(encoding="utf-8"))
    if results["dataset_revision"] != dataset["revision"]:
        raise ValueError("RAG_QUALITY_DATASET_REVISION_MISMATCH")
    by_case = {item["case_id"]: item for item in results["results"]}
    if set(by_case) != {item["case_id"] for item in dataset["cases"]}:
        raise ValueError("RAG_QUALITY_RESULT_CASES_MISMATCH")
    corpus: Any = yaml.safe_load((ROOT / args.corpus).read_text(encoding="utf-8"))
    embedding = DeterministicEmbedding()
    index = LocalHybridIndex(
        tuple(
            LocalIndexRecord(
                item["chunk_id"],
                item["document_version_id"],
                item["text"],
                tuple(embedding.embed([item["text"]])[0]),
            )
            for item in corpus["chunks"]
        )
    )
    context = SearchContext("quality", ("gold",), (), 0, 0, "gold", 0, 0)
    cases = []
    for case in dataset["cases"]:
        query = case["question"]
        candidate_k = max(args.k, 5)
        bm25 = index.search_bm25(query, context, candidate_k)
        dense = index.search_dense(embedding.embed([query])[0], context, candidate_k)
        query_type = classify_query(query)
        fused = rrf_fuse(
            (bm25, dense),
            rrf_k=60,
            channel_weights={
                "bm25": 2.0 if query_type in {"identifier", "keyword"} else 1.0,
                "dense": 1.0,
            },
        )
        cases.append(
            {
                **case,
                **by_case[case["case_id"]],
                "retrieved_chunk_ids": [item[0].chunk_id for item in fused[: args.k]],
            }
        )
    thresholds = json.loads((ROOT / args.thresholds).read_text(encoding="utf-8"))
    report = {
        "dataset_id": dataset["dataset_id"],
        "dataset_revision": dataset["revision"],
        "provider": results["provider"],
        **evaluate_quality(cases, k=args.k, thresholds=thresholds),
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
