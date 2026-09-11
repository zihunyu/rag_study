"""Preview/apply an incremental local directory import; publication remains a review step."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.directory_ledger import SQLiteDirectorySyncLedger  # noqa: E402
from ragkb.application.directory_sync import DirectorySync, digest  # noqa: E402
from ragkb.config.env import SECRET_KEYS, EnvSettings  # noqa: E402
from ragkb.runtime_components import build_runtime_components  # noqa: E402


def processing_settings(settings: EnvSettings) -> dict[str, object]:
    semantic_keys = {
        "rag_runtime_profile",
        "mineru_base_url",
        "mineru_model_version",
        "mineru_enable_table",
        "mineru_enable_formula",
        "embedding_base_url",
        "embedding_model",
        "embedding_dimension",
        "embedding_normalize",
        "embedding_cache_revision",
        "directory_sync_contract_revision",
        "tokenizer_id",
        "tokenizer_artifact_sha256",
        "ocr_enabled",
    }
    if settings.ocr_enabled:
        semantic_keys.update(
            {
                "ocr_base_url",
                "ocr_model",
                "ocr_max_output_tokens",
                "ocr_temperature",
                "ocr_top_p",
                "ocr_prompt_revision",
                "ocr_allowed_output_domains",
                "ocr_max_image_bytes",
                "ocr_max_image_pixels",
                "ocr_max_images_per_document",
                "ocr_max_repair_attempts",
                "ocr_verify_enabled",
                "ocr_verify_base_url",
                "ocr_verify_model",
                "ocr_local_check_enabled",
                "ocr_render_fallback_enabled",
                "ocr_render_max_pages",
                "ocr_render_dpi",
                "ocr_soffice_path",
            }
        )
    # Billing rates, credentials, retries, concurrency and query-only settings do
    # not change stored content and must not trigger a costly corpus rebuild.
    return {
        k: v
        for k, v in settings.model_dump().items()
        if k.upper() not in SECRET_KEYS
        and (k in semantic_keys or k.startswith(("chunk_", "parent_chunk_")))
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--space-id", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Upload changed contents and enqueue ingestion"
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Create new versions for failed/cancelled imports",
    )
    parser.add_argument("--report", type=Path, help="Write the full manifest and per-file results")
    args = parser.parse_args()
    runtime = build_runtime_components(repository_root=ROOT)
    selected = processing_settings(runtime.settings)

    def contract(kind: str) -> str:
        return digest(
            {
                "settings": selected,
                "parser": runtime.parser_router.route(kind).revision,
                "chunker": runtime.chunker.revision,
            }
        )

    try:
        service = DirectorySync(
            runtime.uploads,
            SQLiteDirectorySyncLedger(runtime.storage.root / "sync/directory.sqlite3"),
            contract,
            max_files=runtime.settings.directory_sync_max_files,
        )
        result = service.run(
            args.directory, args.space_id, apply=args.apply, retry_failed=args.retry_failed
        )
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps({k: v for k, v in result.items() if k != "results"}, ensure_ascii=False))
        return 1 if result["failed"] else 0
    finally:
        for transport in runtime.provider_transports:
            transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
