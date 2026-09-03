"""Plan or execute the approved 669-chunk Embedding runner; defaults to plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.provider_http import OpenAIEmbeddingBatchTransport  # noqa: E402
from ragkb.application.provider_runners import (  # noqa: E402
    EmbeddingChunk,
    EmbeddingExecutionRunner,
    embedding_provider_contract,
    require_configured_provider_egress,
    require_embedding_provider_contract,
)
from ragkb.config import load_env  # noqa: E402
from ragkb.document_processing.parsers import ParserRouter  # noqa: E402
from ragkb.engineering_security.file_validation import FORMAT_BY_EXTENSION  # noqa: E402
from ragkb.evaluation.format_samples import _resolve  # noqa: E402
from ragkb.evaluation.local_sample_validation import (  # noqa: E402
    _anonymous_id,
    _expected_locator_match,
)
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402

ATTEMPT_REVISION = "embedding-real-attempt:v2-dashscope-batch10"
ATTEMPT_CHECKPOINT = (
    ROOT / "artifacts/final-validation/provider-checkpoints/embedding-attempt-v2.json"
)
APPROVED_MAX_CHUNKS = 669
APPROVED_MAX_BATCHES = 67


def _attempt_status() -> dict[str, object]:
    if not ATTEMPT_CHECKPOINT.is_file():
        return {
            "approved": False,
            "executed": False,
            "execution_status": "PLANNED",
            "real_request_count": 0,
            "completed_batches": 0,
            "vector_count": 0,
        }
    loaded = json.loads(ATTEMPT_CHECKPOINT.read_text(encoding="utf-8"))
    namespace = loaded.get("embedding", {}) if isinstance(loaded, dict) else {}
    records = [
        value for key, value in namespace.items() if key != "_manifest" and isinstance(value, dict)
    ]
    completed = [value for value in records if value.get("state") == "COMPLETED"]
    return {
        "approved": True,
        "executed": bool(records),
        "execution_status": (
            "COMPLETED" if len(completed) == APPROVED_MAX_BATCHES else "INCOMPLETE"
        ),
        "real_request_count": len(records),
        "completed_batches": len(completed),
        "vector_count": sum(len(value.get("vectors", [])) for value in completed),
    }


def _eligible_chunks() -> list[EmbeddingChunk]:
    plan = yaml.safe_load(
        (ROOT / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    router = ParserRouter()
    chunks: list[EmbeddingChunk] = []
    for item in plan["collection_plan"]:
        category = item["format"]
        if category not in {"pdf_text", "pptx", "spreadsheet"}:
            continue
        directory = _resolve(ROOT, item["sample_directory"])
        metadata = yaml.safe_load(_resolve(ROOT, item["metadata_path"]).read_text(encoding="utf-8"))
        for sample in metadata["samples"]:
            path = (directory / sample["file"]).resolve()
            source_format = FORMAT_BY_EXTENSION[path.suffix.casefold()][0]
            document = router.parse(source_format, path, "private-embedding-snapshot")
            expected = [
                locator for locator in sample["expected_locators"] if isinstance(locator, Mapping)
            ]
            matched, total = _expected_locator_match(
                expected, [node.locator.to_dict() for node in document.nodes]
            )
            if matched != total:
                continue
            sample_id = _anonymous_id(category, sample)
            for index, node in enumerate(document.nodes):
                chunk_id = hashlib.sha256(
                    f"{sample_id}:{index}:{node.locator.to_dict()}".encode(),
                    usedforsecurity=False,
                ).hexdigest()[:24]
                chunks.append(EmbeddingChunk(chunk_id, node.display_text))
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "execute"), default="plan", nargs="?")
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    settings = loaded.settings
    contract = embedding_provider_contract(
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        configured_batch_size=settings.embedding_batch_size,
        chunk_count=APPROVED_MAX_CHUNKS,
        approved_max_batches=APPROVED_MAX_BATCHES,
    )
    if args.mode == "plan":
        attempt_status = _attempt_status()
        print(
            json.dumps(
                {
                    "runner": "embedding-execution-runner:v2",
                    "attempt_revision": ATTEMPT_REVISION,
                    "prior_failed_attempt_ref": "provider-checkpoints/embedding.json",
                    "future_checkpoint_ref": ("provider-checkpoints/embedding-attempt-v2.json"),
                    "max_chunks": APPROVED_MAX_CHUNKS,
                    **contract,
                    **attempt_status,
                    "timeout_seconds": 120,
                    "automatic_retries": 0,
                    "zilliz_write_approved": False,
                    "real_call_performed": bool(attempt_status["executed"]),
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.approved:
        raise RuntimeError("EMBEDDING_EXECUTION_APPROVAL_REQUIRED")
    require_embedding_provider_contract(contract)
    plan = yaml.safe_load(
        (ROOT / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    classifications: list[str] = []
    for item in plan["collection_plan"]:
        if item["format"] not in {"pdf_text", "pptx", "spreadsheet"}:
            continue
        metadata = yaml.safe_load(_resolve(ROOT, item["metadata_path"]).read_text(encoding="utf-8"))
        classifications.extend(
            str(sample.get("source_classification", "")) for sample in metadata["samples"]
        )
    require_configured_provider_egress(
        outbound_ai_allowed=settings.ai_outbound_allowed,
        allowed_classifications=settings.ai_outbound_allowed_classifications,
        approved_processing_regions=settings.ai_approved_processing_regions,
        classifications=classifications,
    )
    chunks = _eligible_chunks()
    if len(chunks) != 669:
        raise RuntimeError("EMBEDDING_ELIGIBLE_SNAPSHOT_COUNT_MISMATCH")
    result = EmbeddingExecutionRunner(
        OpenAIEmbeddingBatchTransport(settings),
        JsonCheckpointStore(ATTEMPT_CHECKPOINT),
        dimension=settings.embedding_dimension,
        external_call_approved=args.approved,
        batch_size=settings.embedding_batch_size,
        max_chunks=APPROVED_MAX_CHUNKS,
        max_batches=APPROVED_MAX_BATCHES,
    ).run(chunks)
    print(
        json.dumps(
            {
                "snapshot_hash": result["snapshot_hash"],
                "chunk_count": result["chunk_count"],
                "batch_count": result["batch_count"],
                "automatic_retries": 0,
                "zilliz_write_performed": False,
                "content_in_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
