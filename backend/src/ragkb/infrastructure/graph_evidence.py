"""Query approved diagram structure without treating Mermaid layout as source evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from typing import Any, Literal

from ragkb.domain.graph_facts import graph_facts, query_graph
from ragkb.domain.visuals import VisualExtraction, VisualQueryOutcome


def _mentioned(label: str, question: str) -> bool:
    value = label.strip()
    if not value:
        return False
    if re.fullmatch(r"[\w .:/-]+", value, flags=re.ASCII):
        return bool(
            re.search(r"(?<![A-Za-z0-9_])" + re.escape(value) + r"(?![A-Za-z0-9_])", question, re.I)
        )
    return value.casefold() in question.casefold()


def approved_graph_evidence(
    asset: dict[str, Any], version_id: str, question: str
) -> VisualQueryOutcome | None:
    """Only human-approved current assets qualify; source authorization is checked by caller.

    Model-approved images still cross original-pixel verification. Partial graphs expose
    only facts whose endpoints and containing groups are confirmed, through graph_facts.
    """
    if asset.get("origin") != "human_review" or asset.get("status") != "verified":
        return None
    if not asset.get("extraction"):
        return None
    extraction = VisualExtraction.model_validate(asset["extraction"])
    partial = any(
        g.uncertainties
        or any(e.review_status in {"pending", "excluded"} for e in [*g.nodes, *g.groups, *g.edges])
        for g in extraction.graphs
    )
    if extraction.kind not in {"diagram", "mixed"} or not extraction.graphs:
        if partial:
            return VisualQueryOutcome(
                "uncertain",
                issues=("该图含已排除或未确认关系；整图重新识别会带回这些内容，请先完成区域复核",),
            )
        return None
    records: list[dict[str, Any]] = []
    truncated = False
    unresolved = []
    for index, graph in enumerate(extraction.graphs):
        scope = f"{version_id}:{asset['id']}:{index}"
        mentions = [
            (question.casefold().find(n.label.casefold()), n.id)
            for n in graph.nodes
            if _mentioned(n.label, question)
        ] + [
            (question.casefold().find(g.label.casefold()), g.id)
            for g in graph.groups
            if _mentioned(g.label, question)
        ]
        mentioned = tuple(identity for _, identity in sorted(mentions))
        graph_question = bool(
            re.search(
                r"流程|架构|连接|关系|路径|步骤|分支|总结|概述|\b(?:flow|architecture|connect|path|branch|overview|summari[sz])",
                question,
                re.I,
            )
        )
        if not mentioned and not graph_question:
            continue
        source_ids = mentioned or tuple(n.id for n in graph.nodes)
        mode: Literal["direct", "paths", "branches"] = "direct"
        targets: tuple[str, ...] = ()
        if (
            re.search(r"路径|经过|怎么到|如何到|\b(?:path|route)\b", question, re.I)
            and len(mentioned) >= 2
        ):
            positions = sorted({position for position, _ in mentions})
            if len(positions) == 2:
                # A repeated name denotes all matching instances until scope resolves it.
                # Never pick the first Nacos/server instance just because of JSON order.
                mode = "paths"
                source_ids = tuple(
                    identity for position, identity in mentions if position == positions[0]
                )
                targets = tuple(
                    identity for position, identity in mentions if position == positions[1]
                )
            else:
                unresolved.append(
                    "问题包含多个中间节点或同名对象，请明确起点、终点及所属分组；以下仅提供已确认连接"
                )
        elif re.search(
            r"失败|成功|如果|分支|怎么办|流程|步骤|\b(?:branch|fail|success|if|workflow)\b",
            question,
            re.I,
        ):
            mode = "branches"
        query = query_graph(
            graph,
            scope=scope,
            verified=True,
            source_ids=source_ids,
            target_ids=targets,
            mode=mode,
            max_hops=6,
            max_results=60,
        )
        unresolved.extend(query.gaps)
        facts = list(query.facts)
        # Nodes/group identities supply explicit scope for every relationship and decision.
        endpoint_ids = {i for f in facts for i in (f.source_id, f.target_id) if i}
        facts += [
            f
            for f in graph_facts(graph, scope=scope, verified=True)
            if f.kind != "edge" and f.element_id in endpoint_ids | set(source_ids)
        ]
        for fact in facts:
            row = {
                **asdict(fact),
                "asset_id": asset["id"],
                "graph_index": index,
                "document_version_id": version_id,
            }
            if row["fact_id"] not in {r["fact_id"] for r in records}:
                records.append(row)
        truncated |= query.truncated
        if mode == "paths" and not query.paths:
            unresolved.append("已确认结构中未找到指定方向的完整路径；不能据此断言不存在其他路径")
    if partial and extraction.tables:
        unresolved.append("同图表格尚未纳入本次部分结构回答；不能从已排除区域恢复表格事实")
    elif not partial:
        for index, table in enumerate(extraction.tables):
            text = "\n".join(filter(None, [table.title, table.as_html(), *table.notes]))
            if len(text) > 12000:
                return VisualQueryOutcome(
                    "uncertain", issues=("同图完整表格超过本轮读取上限，请按表格或章节缩小范围",)
                )
            records.append(
                {
                    "fact_id": "GF-"
                    + hashlib.sha256(
                        f"{version_id}:{asset['id']}:table:{index}".encode()
                    ).hexdigest()[:24],
                    "kind": "table",
                    "element_id": f"table:{index}",
                    "table_index": index,
                    "text": text,
                    "condition": "",
                    "asset_id": asset["id"],
                    "document_version_id": version_id,
                    "review_status": "confirmed",
                    "bbox": None,
                    "bbox_basis": "unverified",
                }
            )
    if not partial and extraction.body_text.strip():
        # Complete human approval includes separately reviewed source footnotes. A
        # diagram's conditions must not disappear merely because they aren't edges.
        body = extraction.body_text.strip()
        if len(body) > 12000:
            return VisualQueryOutcome(
                "uncertain", issues=("人工确认的图注与条件超过本轮完整读取上限，请按章节缩小范围",)
            )
        records.insert(
            0,
            {
                "fact_id": "GF-"
                + hashlib.sha256(f"{version_id}:{asset['id']}:body".encode()).hexdigest()[:24],
                "kind": "note",
                "element_id": "body_text",
                "text": body,
                # Plain notes remain evidence; condition_quotes inventories their actual
                # qualified sentences rather than treating the whole note as one rule.
                "condition": "",
                "asset_id": asset["id"],
                "document_version_id": version_id,
                "review_status": "confirmed",
                "bbox": None,
                "bbox_basis": "unverified",
            },
        )
    if not records:
        if partial:
            return VisualQueryOutcome(
                "uncertain", issues=("已确认子图不能支持本问题，不能重新读取已排除的原图关系",)
            )
        return None
    if not partial and extraction.title.strip():
        # An approved visible title is source identity, not an inferred graph relationship.
        # Keep it independently citable so similarly named services in other figures cannot
        # silently inherit this diagram's product/platform identity.
        records.insert(
            0,
            {
                "fact_id": "GF-"
                + hashlib.sha256(f"{version_id}:{asset['id']}:title".encode()).hexdigest()[:24],
                "kind": "source_context",
                "element_id": "title",
                "text": "图片标题：" + extraction.title.strip(),
                "condition": "",
                "asset_id": asset["id"],
                "document_version_id": version_id,
                "review_status": "confirmed",
                "bbox": None,
                "bbox_basis": "unverified",
            },
        )
    bounded: list[dict[str, Any]] = []
    used = 0
    for row in records:
        if len(bounded) >= 120 or used + len(row["text"]) > 12000:
            truncated = True
            continue
        bounded.append(row)
        used += len(row["text"])
    records = bounded
    if truncated:
        unresolved.append("图关系查询达到本轮路径或步数上限，尚未覆盖所有分支")
    return VisualQueryOutcome(
        "supported",
        "\n".join(r["text"] for r in records),
        unanswered_topics=tuple(unresolved),
        facts=tuple(records),
        truncated=truncated,
    )
