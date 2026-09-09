"""Map the existing synthetic fixtures to separately stored acceptance cases.

No uploads, database writes or model calls. Bindings use the recorded ingestion IDs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ragkb.application.reading_scope import ReadingOptions
from ragkb.domain.acceptance import AcceptanceCase, HistoryQuestion


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixture = root / "backend/tests/fixtures/synthetic_qa_v1"
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    cases = json.loads((fixture / "cases.json").read_text(encoding="utf-8"))["cases"]
    bindings = json.loads(args.bindings.read_text(encoding="utf-8"))
    ids = {key: row["document_id"] for key, row in bindings["documents"].items()}
    grouped: dict[str, list[dict]] = {}
    for case in cases:
        facts = {f for point in case["required_points"] for f in point["fact_ids"]}
        notes = [
            f"{f} · {manifest['facts'][f]['doc_id']}：{manifest['facts'][f]['quote']}"
            for f in sorted(facts)
        ]
        notes.append("预期行为说明：" + case["answer_example"])
        notes.append("来自合成资料 synthetic-qa-v1，参考要求与业务源文件分开存储。")
        reading = case["reading"]
        result = AcceptanceCase(
            key=case["id"],
            question=case["question"],
            category=case["category"],
            reading=ReadingOptions(
                mode=reading["mode"], document_ids=tuple(ids[d] for d in reading["document_keys"])
            ),
            history=[
                HistoryQuestion(
                    question=h["content"],
                    reading=ReadingOptions(
                        mode=h["reading"]["mode"],
                        document_ids=tuple(ids[d] for d in h["reading"]["document_keys"]),
                    ),
                )
                for h in case["history"]
                if h["role"] == "user"
            ],
            expected_status=case["allowed_statuses"][0],
            required_points=[p["meaning"] for p in case["required_points"]]
            + ["资料未提供，不可编造：" + item for item in case["unknown_items"]],
            forbidden_claims=case["forbidden_claims"],
            required_source_documents=[ids[d] for d in case["required_source_documents"]],
            required_retrieved_documents=[
                ids[d] for d in case.get("required_retrieved_documents", [])
            ],
            minimum_distinct_cited_documents=case["minimum_distinct_cited_documents"],
            source_notes="\n".join(notes),
        )
        grouped.setdefault(case["knowledge_base"], []).append(result.model_dump(mode="json"))
    args.output.mkdir(parents=True, exist_ok=True)
    for space, rows in grouped.items():
        path = args.output / f"synthetic-qa-v1-{space}.json"
        path.write_text(
            json.dumps(
                {"space_id": bindings["spaces"][space]["id"], "cases": rows},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"{space}: {len(rows)} candidates -> {path.resolve()}")


if __name__ == "__main__":
    main()
