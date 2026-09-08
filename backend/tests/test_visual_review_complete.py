"""Regression tests for review receipts, graph isolation and historical source protection."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.domain.visual_review import IssueResolution, review_issues, validate_review
from ragkb.domain.visuals import VisualExtraction
from ragkb.infrastructure.visual_assets import VisualAssetStore
from test_visual_pipeline import png
from test_workspace_redesign import upload
from test_workspace_redesign import workspace as workspace_fixture

workspace = workspace_fixture


def extraction():
    return VisualExtraction.model_validate(
        {
            "kind": "diagram",
            "title": "流程",
            "description": "A 去 B",
            "transcription": "A B",
            "body_text": "原说明：A 总是到 B",
            "tables": [],
            "uncertainties": [],
            "graphs": [
                {
                    "direction": "LR",
                    "groups": [{"id": "g", "label": "生产区", "parent": None, "direction": "LR"}],
                    "nodes": [
                        {"id": "a", "label": "A", "group": "g", "shape": "rectangle"},
                        {"id": "b", "label": "B", "group": None, "shape": "rectangle"},
                    ],
                    "edges": [
                        {
                            "source": "a",
                            "target": "b",
                            "label": "失败",
                            "direction": "forward",
                            "style": "solid",
                            "condition": "仅检查失败时",
                        }
                    ],
                    "uncertainties": [],
                }
            ],
        }
    )


def source_asset(value=None):
    return {
        "id": "a" * 32,
        "extraction": (value or extraction()).model_dump(),
        "issues": ["箭头方向不清楚", "节点名称需要核对"],
    }


def receipts(asset):
    return [
        IssueResolution(issue_id=i["id"], disposition="confirmed", reason="已逐项对照原图核实")
        for i in review_issues(asset)
    ]


def test_each_original_issue_needs_unique_audited_disposition():
    asset = source_asset()
    edited = extraction()
    with pytest.raises(ValueError, match="ISSUE_RESOLUTIONS_REQUIRED"):
        validate_review(asset, edited, [])
    r = receipts(asset)
    with pytest.raises(ValueError, match="ISSUE_RESOLUTIONS_REQUIRED"):
        validate_review(asset, edited, [r[0], r[0]])
    validate_review(asset, edited, r)
    r[0].disposition = "corrected"
    r[0].targets = ["graphs/0/edges/0"]
    with pytest.raises(ValueError, match="CORRECTION_MISSING"):
        validate_review(asset, edited, r)
    edited.graphs[0].edges[0].direction = "both"
    validate_review(asset, edited, r)


def test_exclusion_must_preserve_and_close_group_and_edge_dependencies():
    asset = source_asset()
    edited = extraction()
    edited.graphs[0].groups[0].review_status = "excluded"
    with pytest.raises(ValueError, match="EXCLUDED_GROUP_DEPENDENCY"):
        validate_review(asset, edited, receipts(asset))
    edited.graphs[0].nodes[0].review_status = "excluded"
    with pytest.raises(ValueError, match="EXCLUDED_EDGE_DEPENDENCY"):
        validate_review(asset, edited, receipts(asset))
    edited.graphs[0].edges[0].review_status = "excluded"
    r = receipts(asset)
    r[0].disposition = "excluded"
    r[0].targets = ["graphs/0/groups/0"]
    validate_review(asset, edited, r)
    assert edited.graphs[0].edges[0].condition == "仅检查失败时"
    edited.graphs[0].nodes[1].review_status = "pending"
    with pytest.raises(ValueError, match="UNRESOLVED_UNCERTAINTIES"):
        validate_review(asset, edited, r)


def setup_visual(workspace):
    client, runtime, _, space = workspace
    done = upload(client, runtime, space, name="diagram-review.md")
    store = VisualAssetStore(runtime.storage)
    asset = store.save_image(done["document_version_id"], png(), {"part": "image.png"})
    asset.update(
        source_asset(),
        id=asset["id"],
        status="needs_review",
        stage="needs_review",
        section_path="流程",
    )
    store.write_manifest(done["document_version_id"], [asset])
    updated = replace(runtime, settings=runtime.settings.model_copy(update={"ocr_enabled": True}))
    return updated, done, store, asset


def test_review_context_and_receipts_are_version_bound_and_idempotent(workspace):
    runtime, done, store, asset = setup_visual(workspace)
    version = done["document_version_id"]
    with TestClient(create_app(runtime)) as client:
        context = client.get(f"/api/document-versions/{version}/visual-review-context").json()
        assert not context["historical"]
        assert len(context["issues"][asset["id"]]) == 2
        path = f"/api/document-versions/{version}:revise-visuals"
        headers = {
            "If-Match": str(context["row_version"]),
            "Idempotency-Key": "review-with-receipts",
        }
        body = {
            "edits": [{"asset_id": asset["id"], "extraction": extraction().model_dump()}],
            "reason": "逐项核实原图",
            "confirmed_against_original": True,
        }
        denied = client.post(path, headers=headers, json=body)
        assert denied.status_code == 422 and "ISSUE_RESOLUTIONS_REQUIRED" in denied.text
        body["edits"][0]["resolutions"] = [r.model_dump() for r in receipts(asset)]
        result = client.post(path, headers=headers, json=body)
        assert result.status_code == 202, result.text
        assert client.post(path, headers=headers, json=body).json() == result.json()
        session = runtime.repository.get_session(result.json()["upload_session_id"])
        plan = store.ledger.get("session_plan", session.id)
        assert len(plan["history"][0]["resolutions"]) == 2


def test_historical_revision_and_reparse_require_explicit_restore(workspace):
    runtime, done, store, asset = setup_visual(workspace)
    version = done["document_version_id"]
    with TestClient(create_app(runtime)) as client:
        context = client.get(f"/api/document-versions/{version}/visual-review-context").json()
        created = client.post(
            f"/api/document-versions/{version}:reparse-visuals",
            headers={"If-Match": str(context["row_version"]), "Idempotency-Key": "new-current"},
        )
        assert created.status_code == 202, created.text
        context = client.get(f"/api/document-versions/{version}/visual-review-context").json()
        assert context["historical"] and context["latest_version_no"] == 2
        headers = {
            "If-Match": str(context["row_version"]),
            "Idempotency-Key": "historical-revision",
        }
        body = {"retry_assets": [asset["id"]], "reason": "恢复历史原文，已对照最新版本"}
        path = f"/api/document-versions/{version}:revise-visuals"
        assert client.post(path, headers=headers, json=body).status_code == 409
        assert (
            client.post(
                f"/api/document-versions/{version}:reparse-visuals", headers=headers
            ).status_code
            == 409
        )
        body.update(restore_from_history=True, acknowledged_latest_version_id="stale")
        assert client.post(path, headers=headers, json=body).status_code == 409
        body["acknowledged_latest_version_id"] = context["latest_version_id"]
        restored = client.post(path, headers=headers, json=body)
        assert restored.status_code == 202, restored.text
        plan = store.ledger.get("session_plan", restored.json()["upload_session_id"])
        assert (
            plan["restored_from_history"]
            and plan["replaced_latest_version_id"] == context["latest_version_id"]
        )


def test_partial_review_drops_unscoped_prose_without_losing_exclusion_audit(workspace):
    runtime, done, store, asset = setup_visual(workspace)
    value = extraction()
    value.graphs[0].edges[0].review_status = "excluded"
    r = receipts(asset)
    r[0].disposition = "excluded"
    r[0].targets = ["graphs/0/edges/0"]
    with TestClient(create_app(runtime)) as client:
        version = done["document_version_id"]
        context = client.get(f"/api/document-versions/{version}/visual-review-context").json()
        body = {
            "edits": [
                {
                    "asset_id": asset["id"],
                    "extraction": value.model_dump(),
                    "resolutions": [i.model_dump() for i in r],
                }
            ],
            "reason": "仅排除无法确认的失败分支",
            "confirmed_against_original": True,
        }
        response = client.post(
            f"/api/document-versions/{version}:revise-visuals",
            headers={"If-Match": str(context["row_version"]), "Idempotency-Key": "partial-graph"},
            json=body,
        )
        assert response.status_code == 202, response.text
        plan = store.ledger.get("session_plan", response.json()["upload_session_id"])
        saved = plan["edits"][asset["id"]]
        assert saved["body_text"] == saved["description"] == ""
        assert saved["graphs"][0]["edges"][0]["review_status"] == "excluded"
        assert saved["graphs"][0]["edges"][0]["condition"] == "仅检查失败时"
        assert "A 总是到 B" not in VisualExtraction.model_validate(saved).retrieval_text()


def test_review_issues_keep_identical_warnings_in_separate_graph_scopes():
    value = extraction()
    value.graphs[0].uncertainties = ["箭头方向不清楚"]
    value.graphs.append(value.graphs[0].model_copy(deep=True))
    asset = {"id": "a" * 32, "extraction": value.model_dump(), "issues": ["箭头方向不清楚"]}
    issues = review_issues(asset)
    assert len(issues) == 2
    assert issues[0]["id"] != issues[1]["id"]
    assert {i["scope"] for i in issues} == {"graphs/0", "graphs/1"}


def test_graph_issue_cannot_be_resolved_by_unrelated_title_or_metadata_changes():
    asset = source_asset()
    edited = extraction()
    edited.title = "修改过的标题"
    r = receipts(asset)
    r[0].disposition = "corrected"
    r[0].targets = ["title"]
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        validate_review(asset, edited, r)
    r[0].targets = ["graphs/0/edges/0"]
    edited.graphs[0].edges[0].review_status = "confirmed"
    with pytest.raises(ValueError, match="CORRECTION_MISSING"):
        validate_review(asset, edited, r)


def test_public_partial_mermaid_does_not_present_excluded_edges_as_approved():
    value = extraction()
    value.graphs[0].edges[0].review_status = "excluded"
    public = VisualAssetStore.public(
        {"id": "a" * 32, "extraction": value.model_dump(), "status": "verified"}, "version"
    )
    assert public["graph_coverage"][0]["partial"]
    assert public["graph_coverage"][0]["excluded_edges"] == 1
    assert " -->" not in public["mermaid"][0]
    assert public["extraction"]["graphs"][0]["edges"][0]["condition"] == "仅检查失败时"


def test_exclusion_closure_includes_references_inside_an_unindexed_image():
    from ragkb.infrastructure.visual_revisions import exclusion_closure

    class Repository:
        def list_chunks(self, *_args, **_kwargs):
            return []

    assets = {
        "a": {"section_path": "执行步骤", "caption": "图 1"},
        "b": {
            "section_path": "故障处理",
            "extraction": {"body_text": "先按图 1 执行检查，再判断是否恢复。"},
        },
    }
    assert exclusion_closure(Repository(), "v", assets, ["执行步骤"]) == ["执行步骤", "故障处理"]


def test_severity_uses_machine_evidence_and_never_guesses_from_prose():
    asset = source_asset()
    asset["issues"] = [
        "VISUAL_METADATA_TITLE_INFERRED",
        "原图标题为推断，但箭头也可能错了",
        "数值归属冲突",
    ]
    asset["local_check"] = {"status": "disagreement", "issues": ["数值归属冲突"]}
    levels = {i["message"]: i["severity"] for i in review_issues(asset)}
    assert levels == {
        "VISUAL_METADATA_TITLE_INFERRED": "info",
        "原图标题为推断，但箭头也可能错了": "review",
        "数值归属冲突": "blocking",
    }
    r = [
        IssueResolution(issue_id=i["id"], disposition="confirmed", reason="逐项对照原图确认")
        for i in review_issues(asset)
        if i["severity"] != "info"
    ]
    validate_review(asset, extraction(), r)


def test_workspace_actions_explain_visual_quality_block_before_publication(workspace):
    from ragkb.domain.validation import DocumentQualityReport, QualityDisposition

    client, runtime, _, space = workspace
    done = upload(client, runtime, space, name="quality-review-block.md")
    version = done["document_version_id"]
    quality = runtime.repository.get_quality_report(version)
    runtime.repository.save_quality_report(
        DocumentQualityReport(
            version,
            quality["source_format"],
            quality["parser_revision"],
            quality["node_count"],
            quality["locator_coverage"],
            ("VISUAL_REVIEW_REQUIRED:asset",),
            QualityDisposition.BLOCKED_REAL_VALIDATION,
        )
    )
    row = client.get(f"/api/spaces/{space}/documents/{done['document_id']}/workspace").json()
    assert "review_publish" not in row["available_actions"]
    assert "review_quality" in row["available_actions"]
    assert "图片复核尚未完成" in row["unavailability_reasons"][0]


def test_legacy_multi_image_review_remains_version_scoped(tmp_path):
    from ragkb.adapters.local_storage import LocalFileStorage
    from ragkb.document_processing.visual_processing import VisualProcessor
    from ragkb.document_processing.visual_sources import SourceImage
    from ragkb.domain.documents import SourceLocator
    from test_visual_pipeline import EXTRACTION, settings
    from test_visual_workspace import CountingAnalyzer

    store = VisualAssetStore(LocalFileStorage(tmp_path / "store"))
    processor = VisualProcessor(store, CountingAnalyzer(), settings())
    pictures = [SourceImage(png(), SourceLocator(part=f"image-{i}.png")) for i in range(2)]
    source = processor.process("base", pictures, {}, "doc")
    ids = [asset["id"] for _, asset in source]
    store.ledger.put(
        "version_plan",
        "edited",
        {
            "base_version_id": "base",
            "edits": {identity: EXTRACTION for identity in ids},
            "history": [{"reason": "历史批量核对，未记录具体图片"}],
        },
    )
    result = processor.process("edited", pictures, {}, "doc")
    assert all(not asset["history"] for _, asset in result)
    history = store.ledger.get("version", "edited")["legacy_version_history"]
    assert history == [
        {"reason": "历史批量核对，未记录具体图片", "legacy": True, "scope": "version"}
    ]
    assert "asset_id" not in history[0]


def test_legacy_single_image_history_binding_is_idempotent_and_does_not_claim_other_images():
    from ragkb.document_processing.visual_processing import _review_history

    plan = {"edits": {"a": {}}, "history": [{"reason": "原图核对"}]}
    first = _review_history("a", None, plan)
    repeated = _review_history("a", {"history": first}, plan)
    assert repeated == first
    assert first[0]["asset_id"] == "a" and first[0]["legacy"]
    assert _review_history("b", None, plan) == []
