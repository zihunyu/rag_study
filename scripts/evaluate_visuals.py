"""Evaluate stored outputs or explicitly run configured models on a local annotated dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/src"))

from ragkb.config import load_env  # noqa: E402
from ragkb.domain.visual_evaluation import evaluate_pair  # noqa: E402
from ragkb.domain.visuals import VisualExtraction  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--predictions", type=Path, help="Existing JSON map from case ID to extraction"
    )
    parser.add_argument(
        "--run", action="store_true", help="Run configured OCR and independent verification"
    )
    parser.add_argument("--split", choices=["development", "heldout", "all"], default="heldout")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.predictions) == bool(args.run):
        parser.error("Specify exactly one of --predictions or --run")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    predictions = (
        json.loads(args.predictions.read_text(encoding="utf-8")) if args.predictions else {}
    )
    analyzer = None
    if args.run:
        from ragkb.adapters.visual_http import VisualAnalyzer

        analyzer = VisualAnalyzer(load_env().settings)
    cases = []
    for case in dataset["cases"]:
        if args.split != "all" and case["split"] != args.split:
            continue
        if analyzer:
            image_path = (args.dataset.parent / case["image"]).resolve()
            result = analyzer.analyze(image_path.read_bytes())
            predictions[case["id"]] = result.get("extraction")
            state = result["status"]
        else:
            state = "stored_output"
        predicted = predictions.get(case["id"])
        metrics = evaluate_pair(
            VisualExtraction.model_validate(case["gold"]),
            VisualExtraction.model_validate(predicted) if predicted else None,
        )
        cases.append({"id": case["id"], "split": case["split"], "status": state, **metrics})
    report = {
        "dataset": dataset["name"],
        "sample_count": len(cases),
        "split": args.split,
        "model": analyzer.revision if analyzer else "stored_outputs",
        "note": "人工标注合成样本的字段级结果，不代表真实业务文档总体准确率。",
        "cases": cases,
        "predictions": predictions,
    }
    report["totals"] = {
        key: sum(int(case[key]) for case in cases)
        for key in (
            "kind_correct",
            "cells_expected",
            "cells_predicted",
            "cells_correct",
            "nodes_expected",
            "nodes_predicted",
            "nodes_correct",
            "edges_expected",
            "edges_predicted",
            "edges_correct",
        )
    }
    if analyzer:
        analyzer.transport.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.output), "sample_count": len(cases)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
