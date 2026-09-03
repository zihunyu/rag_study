"""Generate offline SBOM, license, dependency and security evidence."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_assurance() -> dict[str, object]:
    python_packages = [
        {
            "name": distribution.metadata["Name"],
            "version": distribution.version,
            "license": distribution.metadata.get("License") or "UNKNOWN",
        }
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    ]
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    npm_packages = [
        {"path": path, "version": details.get("version", "UNKNOWN")}
        for path, details in lock.get("packages", {}).items()
        if path
    ]
    return {
        "revision": "offline-assurance:g5-v1",
        "python_sbom": sorted(python_packages, key=lambda item: str(item["name"]).casefold()),
        "npm_sbom": sorted(npm_packages, key=lambda item: str(item["path"])),
        "license_unknown_count": sum(item["license"] == "UNKNOWN" for item in python_packages),
        "network_scan_performed": False,
        "secret_scan_required": True,
        "docker_used": False,
        "simulated": True,
        "real_acceptance": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_assurance()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "revision": report["revision"],
                "python_package_count": len(report["python_sbom"]),  # type: ignore[arg-type]
                "npm_package_count": len(report["npm_sbom"]),  # type: ignore[arg-type]
                "license_unknown_count": report["license_unknown_count"],
                "network_scan_performed": False,
                "docker_used": False,
                "real_acceptance": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
