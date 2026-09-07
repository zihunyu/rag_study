"""Plan/resume normal version ingestion for currently published legacy documents.

Run without --apply first. Original bytes and state are backed up before submission.
Only the saved plan is applied; withdrawn/different active versions are never revived.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/redesign/general-rag-reprocessing")
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = args.output / "manifest.json"
    with httpx.Client(base_url=args.base_url, timeout=180) as client:

        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response

        if not args.apply:
            if manifest.exists():
                raise RuntimeError("PLAN_EXISTS_USE_NEW_OUTPUT_OR_RESUME_APPLY")
            plan = {"created_at": time.time(), "documents": [], "untouched": []}
            for space in call("GET", "/api/spaces/overview").json()["items"]:
                cursor = None
                while True:
                    page = call(
                        "GET",
                        f"/api/spaces/{space['id']}/documents/preview",
                        params={"limit": 100, **({"cursor": cursor} if cursor else {})},
                    )
                    for document in page.json():
                        identity = document["document_id"]
                        lifecycle = call("GET", f"/api/documents/{identity}/lifecycle").json()
                        if (
                            not document["is_answerable"]
                            or lifecycle["lifecycle_state"] != "ACTIVE"
                        ):
                            plan["untouched"].append(
                                {"document_id": identity, "lifecycle": lifecycle}
                            )
                            continue
                        version = lifecycle["active_version_id"]
                        chunks = call(
                            "GET",
                            f"/api/document-versions/{version}/chunks/preview",
                            params={"limit": 500},
                        ).json()
                        if chunks and all(
                            "token-aware:v3:" in str(c["locator"].get("chunking_revision", ""))
                            for c in chunks
                        ):
                            continue
                        raw = call(
                            "GET", f"/api/document-versions/{version}/original/preview"
                        ).content
                        backup = args.output / (identity + Path(document["filename"]).suffix)
                        backup.write_bytes(raw)
                        plan["documents"].append(
                            {
                                "document_id": identity,
                                "space_id": space["id"],
                                "filename": document["filename"],
                                "mime_type": document["mime_type"] or "application/octet-stream",
                                "old_version_id": version,
                                "lifecycle": lifecycle,
                                "sha256": hashlib.sha256(raw).hexdigest(),
                                "backup": str(backup.resolve()),
                                "status": "planned",
                            }
                        )
                    cursor = page.headers.get("X-Next-Cursor")
                    if not cursor:
                        break
            manifest.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "planned": len(plan["documents"]),
                        "untouched": len(plan["untouched"]),
                        "manifest": str(manifest),
                    }
                )
            )
            return
        plan = json.loads(manifest.read_text(encoding="utf-8"))

        def save():
            temporary = manifest.with_suffix(".tmp")
            temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(manifest)

        for item in plan["documents"]:
            if item["status"] == "published":
                continue
            try:
                doc = item["document_id"]
                life = call("GET", f"/api/documents/{doc}/lifecycle").json()
                if (
                    item.get("new_version_id")
                    and life["lifecycle_state"] == "ACTIVE"
                    and life["active_version_id"] == item["new_version_id"]
                ):
                    current = call(
                        "GET", f"/api/spaces/{item['space_id']}/documents/{doc}/workspace"
                    ).json()
                    if current.get("publication", {}).get("phase") == "published":
                        item["status"] = "published"
                        save()
                        continue
                if (
                    life["lifecycle_state"] != "ACTIVE"
                    or life["active_version_id"] != item["old_version_id"]
                ):
                    raise RuntimeError("ACTIVE_STATE_CHANGED_REVIEW_REQUIRED")
                raw = Path(item["backup"]).read_bytes()
                if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise RuntimeError("BACKUP_CHECKSUM_CHANGED")
                key = "structure-v3-" + item["old_version_id"]
                if "upload_session_id" not in item:
                    current = call(
                        "GET", f"/api/spaces/{item['space_id']}/documents/{doc}/workspace"
                    ).json()
                    response = call(
                        "POST",
                        f"/api/documents/{doc}/versions/upload-sessions",
                        headers={"If-Match": str(current["row_version"]), "Idempotency-Key": key},
                        json={
                            "filename": item["filename"],
                            "expected_size": len(raw),
                            "expected_sha256": item["sha256"],
                            "declared_mime": item["mime_type"],
                        },
                    ).json()
                    item["upload_session_id"] = response["upload_session_id"]
                    save()
                upload = item["upload_session_id"]
                state = call("GET", f"/api/upload-sessions/{upload}").json()
                if state["state"] == "CREATED":
                    state = call(
                        "PUT",
                        f"/api/upload-sessions/{upload}/content",
                        headers={"If-Match": str(state["row_version"])},
                        content=raw,
                    ).json()
                if state["state"] == "UPLOADED":
                    state = call(
                        "POST",
                        f"/api/upload-sessions/{upload}:complete",
                        headers={
                            "If-Match": str(state["row_version"]),
                            "Idempotency-Key": key + "-complete",
                        },
                    ).json()
                item.update(
                    new_version_id=state["document_version_id"],
                    job_id=state["job_id"],
                    status="processing",
                )
                save()
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    job = call("GET", f"/api/ingestion-jobs/{item['job_id']}").json()
                    if job["state"] in ("SUCCEEDED", "FAILED_FINAL", "CANCELLED"):
                        break
                    time.sleep(1)
                if job["state"] != "SUCCEEDED":
                    raise RuntimeError("PROCESSING_" + job["state"])
                quality = call(
                    "GET", f"/api/document-versions/{item['new_version_id']}/quality-report"
                ).json()
                item["quality"] = quality
                if quality["disposition"] == "BLOCKED_REAL_VALIDATION" or not quality["node_count"]:
                    raise RuntimeError("QUALITY_REVIEW_REQUIRED")
                life = call("GET", f"/api/documents/{doc}/lifecycle").json()
                if (
                    life["lifecycle_state"] != "ACTIVE"
                    or life["active_version_id"] != item["old_version_id"]
                ):
                    raise RuntimeError("ACTIVE_STATE_CHANGED_REVIEW_REQUIRED")
                result = call(
                    "POST",
                    f"/api/document-versions/{item['new_version_id']}:review-and-publish",
                    headers={
                        "If-Match": str(life["row_version"]),
                        "Idempotency-Key": key + "-publish",
                    },
                    json={
                        "comment": (
                            "重新处理已发布原文件；SHA-256一致，"
                            "通用结构与定位升级，质量报告已核对。"
                        )
                    },
                ).json()
                if result["phase"] != "published":
                    raise RuntimeError("PUBLICATION_INCOMPLETE")
                item.update(status="published", publication=result)
            except Exception as error:
                item.update(status="failed", error=type(error).__name__)
                if isinstance(error, RuntimeError):
                    item["error"] = str(error)
            save()
            print(
                json.dumps(
                    {
                        "filename": item["filename"],
                        "status": item["status"],
                        "error": item.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        for item in plan["untouched"]:
            current = call("GET", f"/api/documents/{item['document_id']}/lifecycle").json()
            item["unchanged"] = current == item["lifecycle"]
        save()


if __name__ == "__main__":
    main()
