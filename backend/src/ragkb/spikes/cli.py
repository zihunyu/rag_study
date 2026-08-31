"""Run one or all G0 Spike harnesses."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from ragkb.config.loader import find_repository_root, load_configuration
from ragkb.spikes.capacity import run_capacity_spike
from ragkb.spikes.milvus import run_milvus_spike
from ragkb.spikes.mineru import run_mineru_spike
from ragkb.spikes.models import run_model_spike
from ragkb.spikes.security import run_security_spike


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run G0 Spike harnesses")
    parser.add_argument(
        "spike",
        nargs="?",
        choices=["mineru", "milvus", "models", "capacity", "security"],
    )
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/g0/spikes"))
    parser.add_argument(
        "--mineru-manifest",
        type=Path,
        default=Path("config/spikes/mineru-samples.yaml"),
    )
    args = parser.parse_args(argv)
    if not args.all and not args.spike:
        parser.error("choose a spike or pass --all")
    root = find_repository_root()
    loaded = load_configuration(root)
    manifest = args.mineru_manifest
    if not manifest.is_absolute():
        manifest = root / manifest
    runners: dict[str, Callable[[], dict[str, object]]] = {
        "mineru": lambda: run_mineru_spike(loaded, manifest),
        "milvus": lambda: run_milvus_spike(loaded),
        "models": lambda: run_model_spike(loaded),
        "capacity": lambda: run_capacity_spike(loaded),
        "security": lambda: run_security_spike(loaded),
    }
    selected = list(runners) if args.all else [str(args.spike)]
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    summary: dict[str, object] = {}
    for name in selected:
        report = runners[name]()
        failed = failed or not bool(report["harness_passed"])
        (output_dir / f"{name}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary[name] = {
            "harness_passed": report["harness_passed"],
            "real_gate_status": report["real_gate_status"],
            "blocker_count": len(report["blockers"]),  # type: ignore[arg-type]
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
