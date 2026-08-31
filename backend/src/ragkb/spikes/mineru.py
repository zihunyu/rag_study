"""MinerU output and real-sample coverage harness."""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from ragkb.adapters.mineru import MinerUEndpoint, MinerURouteDenied, MinerURouter
from ragkb.config.models import LoadedConfiguration
from ragkb.spikes.common import is_stubbed, result

REQUIRED_FORMAT_COUNTS = {
    "pdf_text": 10,
    "pdf_scanned_or_image": 10,
    "docx": 10,
    "pptx": 10,
    "spreadsheet": 10,
    "audio": 10,
}


def _manifest(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        return {"samples": []}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, Mapping) else {"samples": []}


def _validate_output(output: Path, schema: Mapping[str, Any]) -> list[str]:
    if not output.is_file():
        return ["output_missing"]
    try:
        document = json.loads(output.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(document)
    except (json.JSONDecodeError, jsonschema.ValidationError) as error:
        return [f"canonical_contract_invalid:{error.__class__.__name__}"]
    issues: list[str] = []
    nodes = document.get("nodes", [])
    if not nodes:
        issues.append("empty_nodes")
    if any(not str(node.get("original_text", "")).strip() for node in nodes):
        issues.append("empty_node_text")
    if any(not node.get("locator") for node in nodes):
        issues.append("invalid_locator")
    return issues


def run_mineru_spike(loaded: LoadedConfiguration, manifest_path: Path) -> dict[str, object]:
    manifest = _manifest(manifest_path)
    samples = manifest.get("samples", [])
    samples = samples if isinstance(samples, list) else []
    collection_plan = manifest.get("collection_plan", [])
    collection_plan = collection_plan if isinstance(collection_plan, list) else []
    planned_counts = {
        str(item.get("format")): int(item.get("required_count", 0))
        for item in collection_plan
        if isinstance(item, Mapping)
    }
    collection_plan_complete = all(
        planned_counts.get(name) == required for name, required in REQUIRED_FORMAT_COUNTS.items()
    )
    counts = Counter(
        str(item.get("format"))
        for item in samples
        if isinstance(item, Mapping) and item.get("real_sample") is True
    )
    schema = json.loads(
        (loaded.repository_root / "config/schema/canonical-document.schema.json").read_text(
            encoding="utf-8"
        )
    )
    sample_issues: dict[str, list[str]] = {}
    for item in samples:
        if not isinstance(item, Mapping):
            continue
        output_value = item.get("canonical_output")
        if not output_value:
            continue
        output = Path(str(output_value))
        if not output.is_absolute():
            output = loaded.repository_root / output
        issues = _validate_output(output, schema)
        if issues:
            sample_issues[str(item.get("id", "unnamed"))] = issues

    blockers = [
        f"real_samples.{name}: need {required}, found {counts[name]}"
        for name, required in REQUIRED_FORMAT_COUNTS.items()
        if counts[name] < required
    ]
    for path in ("ai_services.mineru.mode", "ai_services.mineru.version"):
        if is_stubbed(loaded.stubbed_paths, path):
            blockers.append(path)
    if sample_issues:
        blockers.append("canonical_output_validation_failed")
    mineru_config = loaded.user["ai_services"]["mineru"]
    router = MinerURouter(
        MinerUEndpoint("self_hosted", mineru_config["self_hosted_endpoint"], True),
        MinerUEndpoint(
            "hosted", mineru_config["hosted_api_endpoint"], mineru_config["hosted_api_enabled"]
        ),
        frozenset(mineru_config["hosted_api_allowed_data_classifications"]),
    )
    confidential_self_hosted = router.select("confidential").kind == "self_hosted"
    restricted_denied = False
    try:
        router.select("restricted", self_hosted_available=False, provider_region_approved=True)
    except MinerURouteDenied:
        restricted_denied = True
    hosted_waits_for_region = False
    try:
        router.select("confidential", self_hosted_available=False)
    except MinerURouteDenied:
        hosted_waits_for_region = True
    if not loaded.user["security_compliance"].get("approved_ai_processing_regions"):
        blockers.append("security_compliance.approved_ai_processing_regions")
    if importlib.util.find_spec("mineru") is None:
        blockers.append("native_mineru_package_not_available")
    mineru_secret = next(item for item in loaded.secret_statuses if item.name == "MINERU_TOKEN")
    if mineru_config["hosted_api_enabled"] and not mineru_secret.configured:
        blockers.append("env:MINERU_TOKEN")
    assertions = [
        {"name": "manifest_is_mapping", "passed": isinstance(manifest, Mapping)},
        {"name": "g0_sixty_slot_collection_plan", "passed": collection_plan_complete},
        {"name": "canonical_schema_is_valid", "passed": bool(schema.get("$schema"))},
        {"name": "provided_outputs_follow_contract", "passed": not sample_issues},
        {"name": "self_hosted_is_primary", "passed": confidential_self_hosted},
        {"name": "restricted_never_uses_hosted_api", "passed": restricted_denied},
        {"name": "hosted_waits_for_region_approval", "passed": hosted_waits_for_region},
    ]
    return result(
        "mineru_format_and_locator",
        assertions,
        blockers,
        {
            "required_real_samples": sum(REQUIRED_FORMAT_COUNTS.values()),
            "planned_sample_slots": sum(planned_counts.values()),
            "planned_format_counts": planned_counts,
            "provided_real_samples": sum(counts.values()),
            "format_counts": dict(counts),
            "sample_issues": sample_issues,
        },
    )
