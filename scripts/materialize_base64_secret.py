"""Decode a base64 environment secret into RUNNER_TEMP without printing its contents."""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runner_temp = Path(os.environ.get("RUNNER_TEMP", "")).resolve()
    output = args.output.resolve()
    if not str(runner_temp) or not output.is_relative_to(runner_temp):
        raise SystemExit("SECRET_OUTPUT_MUST_BE_UNDER_RUNNER_TEMP")
    encoded = os.environ.get(args.env, "")
    if not encoded:
        raise SystemExit(f"{args.env}_REQUIRED")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise SystemExit(f"{args.env}_BASE64_INVALID") from error
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    print(f"materialized={output.name} bytes={len(content)} content_output=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
