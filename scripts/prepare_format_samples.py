"""Create ignored G4 real-format sample landing directories and empty metadata files."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.format_samples import prepare_format_sample_landing  # noqa: E402


def main() -> int:
    paths = prepare_format_sample_landing(
        ROOT, ROOT / "backend/tests/fixtures/manifests/format-samples.yaml"
    )
    print(f"g4_sample_directories={len(paths)} real_samples_created=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
