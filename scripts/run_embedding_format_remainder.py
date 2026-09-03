"""Plan or execute the isolated 459-chunk format-remainder Embedding attempt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.provider_http import OpenAIEmbeddingBatchTransport  # noqa: E402
from ragkb.application.provider_runners import (  # noqa: E402
    EmbeddingExecutionRunner,
    embedding_provider_contract,
    require_configured_provider_egress,
    require_embedding_provider_contract,
)
from ragkb.config import load_env  # noqa: E402
from ragkb.evaluation.embedding_remainder import (  # noqa: E402
    load_format_remainder_chunks,
)
from ragkb.evaluation.format_samples import _resolve  # noqa: E402
from ragkb.evaluation.real_format_validation import (  # noqa: E402
    build_real_format_validation,
)
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402

from scripts.prepare_docx_pdf_inputs import (  # noqa: E402
    validate_prepared_docx_pdf_inputs,
)

ATTEMPT_REVISION = "embedding-real-attempt:v3-format-remainder"
CHECKPOINT = (
    ROOT / "artifacts/final-validation/provider-checkpoints/"
    "embedding-format-remainder-attempt-v3.json"
)


def _status() -> dict[str, object]:
    if not CHECKPOINT.is_file():
        return {
            "executed": False,
            "execution_status": "PLANNED",
            "real_request_count": 0,
            "completed_batches": 0,
            "vector_count": 0,
        }
    loaded = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    namespace = loaded.get("embedding", {})
    records = [
        value for key, value in namespace.items() if key != "_manifest" and isinstance(value, dict)
    ]
    completed = [value for value in records if value.get("state") == "COMPLETED"]
    return {
        "executed": bool(records),
        "execution_status": "COMPLETED" if len(completed) == 46 else "INCOMPLETE",
        "real_request_count": len(records),
        "completed_batches": len(completed),
        "vector_count": sum(len(value.get("vectors", [])) for value in completed),
    }


def _classifications() -> list[str]:
    plan = yaml.safe_load(
        (ROOT / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    values: list[str] = []
    for category in ("pdf_scanned_or_image", "docx"):
        item = next(value for value in plan["collection_plan"] if value["format"] == category)
        metadata = yaml.safe_load(
            _resolve(ROOT, str(item["metadata_path"])).read_text(encoding="utf-8")
        )
        values.extend(
            str(sample.get("source_classification", "")) for sample in metadata["samples"]
        )
    return values


def build_plan() -> dict[str, object]:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    settings = loaded.settings
    contract = embedding_provider_contract(
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        configured_batch_size=settings.embedding_batch_size,
        chunk_count=459,
        approved_max_batches=46,
    )
    chunks, input_evidence = load_format_remainder_chunks(ROOT)
    if len(chunks) != 459:
        raise RuntimeError("EMBEDDING_V3_INPUT_COUNT_MISMATCH")
    status = _status()
    return {
        "revision": "embedding-v3-execution-plan:v1",
        "attempt_revision": ATTEMPT_REVISION,
        "checkpoint_ref": "provider-checkpoints/embedding-format-remainder-attempt-v3.json",
        "approved_by_user": True,
        "runner_review_required_before_execution": not bool(status["executed"]),
        "max_chunks": 459,
        "batch_size": 10,
        "max_batches": 46,
        "automatic_retries": 0,
        "zilliz_write_approved": False,
        "reuses_embedding_v2_checkpoint": False,
        "input_evidence": input_evidence,
        "provider_contract": contract,
        **status,
        "content_output": False,
        "source_names_output": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "execute"), default="plan", nargs="?")
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    if args.mode == "plan":
        print(json.dumps(build_plan(), sort_keys=True))
        return 0
    if not args.approved:
        raise RuntimeError("EMBEDDING_V3_EXECUTION_APPROVAL_REQUIRED")
    if CHECKPOINT.exists():
        raise RuntimeError("EMBEDDING_V3_CHECKPOINT_NOT_EMPTY")
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    settings = loaded.settings
    contract = embedding_provider_contract(
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        configured_batch_size=settings.embedding_batch_size,
        chunk_count=459,
        approved_max_batches=46,
    )
    require_embedding_provider_contract(contract)
    real_format = build_real_format_validation(ROOT)
    if real_format.get("real_acceptance") is not True:
        raise RuntimeError("REAL_FORMAT_ACCEPTANCE_REQUIRED")
    prepared_docx = validate_prepared_docx_pdf_inputs()
    if (
        prepared_docx.get("converted_count") != 10
        or prepared_docx.get("expected_pages_covered_count") != 10
    ):
        raise RuntimeError("EMBEDDING_V3_DERIVED_INPUT_INTEGRITY_REQUIRED")
    require_configured_provider_egress(
        outbound_ai_allowed=settings.ai_outbound_allowed,
        allowed_classifications=settings.ai_outbound_allowed_classifications,
        approved_processing_regions=settings.ai_approved_processing_regions,
        classifications=_classifications(),
    )
    chunks, _ = load_format_remainder_chunks(ROOT)
    result = EmbeddingExecutionRunner(
        OpenAIEmbeddingBatchTransport(settings),
        JsonCheckpointStore(CHECKPOINT),
        dimension=settings.embedding_dimension,
        external_call_approved=True,
        batch_size=10,
        max_chunks=459,
        max_batches=46,
    ).run(chunks)
    print(
        json.dumps(
            {
                "attempt_revision": ATTEMPT_REVISION,
                "chunk_count": result["chunk_count"],
                "batch_count": result["batch_count"],
                "automatic_retries": 0,
                "zilliz_write_performed": False,
                "content_output": False,
                "source_names_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
