"""Source-anchored obligations: checking answer truth is not checking omitted conditions."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from ragkb.domain.rag import DraftAnswer, Evidence

_CONDITION = re.compile(
    r"仅(?:限|当|在|适用|支持|提供|允许)?|必须|不得|禁止|不能|不支持|不适用|不包括|不包含|不涵盖|不含|不在|不予|免除|"
    r"需(?:要|先|提供|满足|经过)|应当|须|"
    r"除外|除非|否则|如果|假如|若|当.{0,60}时|前提|限定条件|适用范围|注意事项|"
    r"至少|至多|不超过|不得超过|上限|下限|有效期|条件[：:]|范围[：:]|"
    r"\b(?:only|unless|except|otherwise|if|when|must|required|shall|cannot|"
    r"not|never|provided\s+that|subject\s+to|at\s+least|at\s+most|maximum|minimum)\b|"
    r"[<>≤≥]=?\s*\d",
    re.I,
)
_BRANCH = re.compile(
    r"(?:是|否|成功|失败|通过|未通过|正常|异常|yes|no|true|false|success|failure)\s*[：:]?\s*(?:→|->|⇒)|"
    r"(?:指向|双向连接|相连|→|->).+[；;]\s*(?:条件[：:]\s*)?(?:是|否|成功|失败|通过|未通过|正常|异常|yes|no|true|false)(?:[。.]|$)",
    re.I,
)


class ConditionCheckError(ValueError):
    """Stable public code with private, source-bound failure details."""

    def __init__(self, code: str, reason: str, expected: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.diagnostic = {
            "code": code,
            "reason": reason,
            "condition_id": (expected or {}).get("id"),
            "evidence_id": (expected or {}).get("evidence_id"),
        }


def partition_condition_checks(
    raw: Any,
    pending: list[dict[str, Any]],
    draft: DraftAnswer,
    question: str,
    required: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]], list[ConditionCheckError]]:
    """Keep uniquely identified, fully validated rows; never infer a missing verdict."""
    expected = {r["id"] for r in pending}
    if not isinstance(raw, list) or any(
        not isinstance(c, dict) or not isinstance(c.get("id"), str) or c["id"] not in expected
        for c in raw
    ):
        error = ConditionCheckError(
            "VERIFIER_CONDITION_CHECK_REQUIRED", "check_shape_or_id_mismatch"
        )
        error.diagnostic.update(
            expected_count=len(pending), received_count=len(raw) if isinstance(raw, list) else None
        )
        return {}, pending, [error]
    checked, failed, errors = {}, [], []
    for rule in pending:
        matches = [c for c in raw if c["id"] == rule["id"]]
        try:
            if len(matches) != 1:
                error = ConditionCheckError(
                    "VERIFIER_CONDITION_CHECK_REQUIRED",
                    "condition_id_missing" if not matches else "condition_id_duplicated",
                    rule,
                )
                error.diagnostic.update(expected_count=len(pending), received_count=len(raw))
                raise error
            checked[rule["id"]] = validate_condition_checks(
                matches, [rule], draft, question, witness_requirements=required
            )[0]
        except ConditionCheckError as caught:
            failed.append(rule)
            errors.append(caught)
    return checked, failed, errors


def _terms(text: str) -> set[str]:
    text = re.sub(r"\[E[^\]]+\]", "", text).casefold()
    text = re.sub(
        r"设备|产品|系统|资料|内容|相关|可以|支持|提供|仅限|位于|包括|不包括|适用|条件", "", text
    )
    result = {
        word
        for word in re.findall(r"[a-z]{2,}|\d+(?:\.\d+)?", text)
        if word
        not in {
            "the",
            "is",
            "are",
            "and",
            "or",
            "in",
            "of",
            "to",
            "for",
            "only",
            "must",
            "device",
            "product",
            "system",
        }
    }
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(part[i : i + 2] for i in range(len(part) - 1))
    return result


def _has_condition_witness(source: str, quote: str) -> bool:
    """Reject vacuous 'covered' witnesses; semantic review still checks their meaning."""

    def anchors(text: str) -> set[str]:
        text = re.sub(r"\[E[^\]]+\]", "", text).casefold()
        text = re.sub(
            r"设备|产品|系统|资料|内容|相关|可以|支持|提供|仅限|位于|包括|不包括|适用|条件",
            "",
            text,
        )
        terms = {
            word
            for word in re.findall(r"[a-z]{2,}|\d+(?:\.\d+)?", text)
            if word
            not in {
                "the",
                "is",
                "are",
                "and",
                "or",
                "in",
                "of",
                "to",
                "for",
                "only",
                "must",
                "device",
                "product",
                "system",
            }
        }
        for part in re.findall(r"[\u4e00-\u9fff]+", text):
            terms.update(part[i : i + 2] for i in range(len(part) - 1))
        return terms

    if anchors(source).intersection(anchors(quote)):
        return True
    different_language = bool(re.search(r"[\u4e00-\u9fff]", source)) != bool(
        re.search(r"[\u4e00-\u9fff]", quote)
    )
    return different_language and bool(_CONDITION.search(quote))


def _witness_text(value: str) -> tuple[str, list[int]]:
    ignored: set[int] = set()
    for match in re.finditer(r"\[E\d+\]", value):
        ignored.update(range(match.start(), match.end()))
    for match in re.finditer(r"\*\*(.+?)\*\*|(?<!\w)__(.+?)__(?!\w)", value, re.S):
        ignored.update([match.start(), match.start() + 1, match.end() - 2, match.end() - 1])
    text: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(value):
        if index in ignored or character.isspace():
            continue
        normalized = unicodedata.normalize("NFKC", character)
        text.extend(normalized)
        positions.extend([index] * len(normalized))
    return "".join(text), positions


def resolve_condition_witness(
    quote: str, draft: DraftAnswer, expected: dict[str, Any], required: list[dict[str, Any]]
) -> str:
    """Recover only a unique, already displayed span; never repair factual wording.

    Some providers move the paragraph citation or add a final period when quoting a
    semicolon clause. Formatting recovery must preserve all substantive characters.
    Pronouns may use one unambiguous decision explicitly named in the same paragraph.
    """
    if quote not in draft.text:
        if set(re.findall(r"\[(E\d+)\]", quote)) - set(draft.citation_ids):
            return quote
        needle, _ = _witness_text(quote)
        needle = needle.rstrip(".。!?！？;；")
        haystack, offsets = _witness_text(draft.text)
        start = haystack.find(needle) if len(needle) >= 6 else -1
        if start < 0 or haystack.find(needle, start + 1) >= 0:
            return quote
        quote = draft.text[offsets[start] : offsets[start + len(needle) - 1] + 1]
    if expected.get("kind") != "workflow_branch" or not quote or quote not in draft.text:
        return quote
    if _terms(str(expected.get("source", ""))).intersection(_terms(quote)):
        return quote
    start = draft.text.find(quote)
    if draft.text.find(quote, start + 1) >= 0:
        return quote
    begin = draft.text.rfind("\n\n", 0, start) + 2 if "\n\n" in draft.text[:start] else 0
    end = draft.text.find("\n\n", start + len(quote))
    paragraph = draft.text[begin : end if end >= 0 else len(draft.text)]
    if len(paragraph) > 3000:
        return quote
    plain, _ = _witness_text(paragraph)
    names = {
        _witness_text(str(r.get("source", "")))[0]
        for r in required
        if r.get("kind") == "workflow_branch"
    }
    matched = {name for name in names if name and name in plain}
    name, _ = _witness_text(str(expected.get("source", "")))
    return paragraph if matched == {name} else quote


def condition_quotes(text: str) -> list[str]:
    # Preserve each complete source sentence / table row, including its qualifier.
    # Dots in decimals and English abbreviations are deliberately not split.
    result = []
    table_conditions = False
    parts = re.split(r"(?<=[。！？!?])|\n", text)
    for index, part in enumerate(parts):
        value = part.strip()
        if value.startswith("|"):
            if not re.search(r"[^\s|:\-]", value):
                continue
            if (
                index + 1 < len(parts)
                and re.fullmatch(r"[\s|:\-]+", parts[index + 1])
                and "---" in parts[index + 1]
            ):
                table_conditions = bool(
                    re.search(
                        r"条件|适用|地区|区域|范围|例外|限制|前提|\b(condition|region|scope|exception|eligibility)\b",
                        value,
                        re.I,
                    )
                )
                continue
            if table_conditions and re.search(r"[^\s|:\-]", value):
                result.append(value)
        else:
            table_conditions = False
        if (
            value
            and not value.startswith("#")
            and (_CONDITION.search(value) or _BRANCH.search(value))
        ):
            result.append(value)
    return list(dict.fromkeys(result))


def condition_requirements(evidence: tuple[Evidence, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in evidence:
        # Retrieval text repeats titles/section paths for ranking. Only the
        # displayed source (including verified visual additions) states rules.
        # A heading named "Exceptions" is not itself an exception to preserve.
        source_text = item.display_text or item.text
        structured = {
            fact["text"]: fact
            for fact in item.locator.get("visual_facts", [])
            if isinstance(fact, dict)
            and fact.get("condition")
            and isinstance(fact.get("text"), str)
            and fact["text"] in item.text
        }
        coverage_quote = item.locator.get("visual_coverage_quote")
        coverage = (
            [coverage_quote]
            if isinstance(coverage_quote, str) and coverage_quote and coverage_quote in item.text
            else []
        )
        anchors = item.locator.get("condition_anchors", {})
        saved = anchors.get("quotes") if isinstance(anchors, dict) else None
        quotes = (
            saved
            if (
                anchors.get("revision") == "qa-source-anchors-v1"
                and anchors.get("text_sha256") == hashlib.sha256(source_text.encode()).hexdigest()
                and isinstance(saved, list)
                and all(isinstance(q, str) and q in source_text for q in saved)
            )
            else condition_quotes(source_text)
        )
        for quote in dict.fromkeys([*quotes, *structured, *coverage]):
            preceding = re.split(
                r"[。！？!?\n]", source_text[: source_text.find(quote)].rstrip("。！？!?\n ")
            )[-1]
            applicability = (
                str(item.locator.get("section_path", "")) + " " + preceding[-180:] + " " + quote
            )
            if quote in structured:
                applicability = quote
            key = (
                item.document_version_id,
                str(item.locator.get("section_path", item.chunk_id)),
                quote,
            )
            if key in seen:
                seen[key]["equivalent_evidence_ids"].append(item.evidence_id)
                continue
            requirement = {
                "id": f"K{len(result) + 1}",
                "evidence_id": item.evidence_id,
                "source_quote": quote,
                "equivalent_evidence_ids": [item.evidence_id],
                "applicability_context": applicability,
                # A shared section title / product name does not establish that
                # two rules concern the same topic. Retain preceding text only
                # for an elliptical restriction such as 'only within the city'.
                "rule_context": (
                    preceding[-180:] + " " + quote
                    if re.match(
                        r"仅|必须|不得|禁止|不包括|不包含|除外|除非|\b(?:only|unless|except|must)\b",
                        quote,
                        re.I,
                    )
                    else quote
                ),
            }
            if quote in structured:
                fact = structured[quote]
                requirement.update(
                    {
                        "kind": "source_condition"
                        if fact.get("kind") == "note"
                        else "workflow_branch",
                        "fact_id": fact["fact_id"],
                        "source": fact.get("source", ""),
                        "target": fact.get("target", ""),
                        "condition": fact["condition"],
                    }
                )
            if quote in coverage:
                requirement["kind"] = "coverage_limit"
            seen[key] = requirement
            result.append(requirement)
    return result


def _local_constraint_text(source: str) -> str:
    """References to another rule need semantic checking across its actual sources.

    'Exclusions follow the warranty terms' does not itself negate an entitlement.
    Keep the complete original requirement for the verifier; omit only a pure
    reference clause from the local conjunction/negation word checks.
    """
    clauses = []
    for clause in re.split(r"[；;]", source):
        reference = re.fullmatch(
            r"[^，,；;。!?！？]*(?:按|依照|参照|以)[^，,；;。!?！？]{1,80}"
            r"(?:条款|规定|规则|政策|要求)(?:执行|处理|办理|为准)[。.]?",
            clause.strip(),
        )
        if reference and not re.search(r"仅限|不得|禁止|必须|不允许", clause):
            continue
        if re.fullmatch(
            r"[^，,；;。!?！？]{1,100}(?:条款|规定|规则|政策|要求)"
            r"(?:仅|只)在满足(?:其中|其)(?:全部|所有)条件时适用[。.]?",
            clause.strip(),
        ):
            # A pure reference to another policy's full conditions has no new
            # concrete conjunct to match by keywords. Keep the original rule in
            # the semantic review: its cited, actual conditions must be checked.
            # Never demand the words '满足全部' in an otherwise complete witness.
            continue
        clauses.append(clause)
    return "；".join(clauses)


def answer_witness_spans(answer: str) -> dict[str, str]:
    """Stable paragraph choices avoid asking a model to reconstruct exact prose."""
    return {
        f"A{i}": paragraph
        for i, paragraph in enumerate((p for p in re.split(r"\n\s*\n", answer) if p.strip()), 1)
    }


def _selected_witness(check: dict[str, Any], answer: str, expected: dict[str, Any]) -> str | None:
    """Resolve multiple real paragraphs without accepting model-reconstructed quotations."""
    ids = check.get("answer_span_ids")
    if ids is None:
        return None
    spans = answer_witness_spans(answer)
    if (
        check.get("status") != "covered"
        or not isinstance(ids, list)
        or not 1 <= len(ids) <= len(spans)
        or any(not isinstance(i, str) or i not in spans for i in ids)
        or len(set(ids)) != len(ids)
        or (check.get("answer_span_id") and check["answer_span_id"] not in ids)
    ):
        raise ConditionCheckError(
            "VERIFIER_CONDITION_WITNESS_INVALID", "answer_span_ids_invalid", expected
        )
    # Keep the exact continuous range, including intervening paragraphs. Semantic
    # review still must check entity, scope and branch bindings across the selection.
    selected = set(ids)
    offset, bounds = 0, []
    for identity, paragraph in spans.items():
        start = answer.index(paragraph, offset)
        offset = start + len(paragraph)
        if identity in selected:
            bounds.append((start, offset))
    return answer[bounds[0][0] : bounds[-1][1]]


def validate_condition_checks(
    raw: Any,
    required: list[dict[str, Any]],
    draft: DraftAnswer,
    question: str = "",
    *,
    witness_requirements: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, str], ...]:
    if not required:
        return ()
    if not isinstance(raw, list) or len(raw) != len(required):
        raise ConditionCheckError("VERIFIER_CONDITION_CHECK_REQUIRED", "check_count_mismatch")
    checks = []
    for expected, check in zip(required, raw, strict=True):
        if not isinstance(check, dict) or check.get("id") != expected["id"]:
            raise ConditionCheckError(
                "VERIFIER_CONDITION_ID_INVALID", "id_or_order_mismatch", expected
            )
        status = check.get("status")
        quote, reason = check.get("answer_quote"), check.get("reason")
        if "applicable" in check and (
            not isinstance(check["applicable"], bool)
            or (check["applicable"] is False and status != "not_applicable")
            or (check["applicable"] is True and status == "not_applicable")
        ):
            raise ConditionCheckError(
                "VERIFIER_CONDITION_VERDICT_INVALID", "applicability_status_mismatch", expected
            )
        selected_quote = _selected_witness(check, draft.text, expected)
        span_id = check.get("answer_span_id")
        if selected_quote is not None:
            quote = selected_quote
        elif span_id:
            spans = answer_witness_spans(draft.text)
            if status != "covered" or not isinstance(span_id, str) or span_id not in spans:
                raise ConditionCheckError(
                    "VERIFIER_CONDITION_WITNESS_INVALID", "answer_span_id_invalid", expected
                )
            quote = spans[span_id]
        if status == "covered" and isinstance(quote, str):
            quote = resolve_condition_witness(
                quote,
                draft,
                expected,
                required if witness_requirements is None else witness_requirements,
            )
        if (
            status not in {"covered", "missing", "not_applicable"}
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ConditionCheckError(
                "VERIFIER_CONDITION_VERDICT_INVALID", "invalid_status_or_reason", expected
            )
        if status in {"covered", "missing"} and re.search(
            r"(?<!not )\b(?:is|are|falls) outside (?:that |this |the )?scope[.,;。]|"
            r"\b(?:this|the|that) (?:rule|condition|restriction) (?:is|does) "
            r"(?:not (?:apply|relate|pertain)|not relevant|not applicable|irrelevant|unrelated)\b|"
            r"(?:该|此|本条)(?:规则|条件|限制).{0,12}(?:与(?:本题|问题|提问)无关|"
            r"不适用于(?:本题|问题|提问)|不属于(?:本题|问题|提问)(?:的)?范围)[，。；;,.]|"
            r"\b(?:rule|condition|restriction) (?:concerns|describes) .{1,100}, "
            r"(?:which is |and is )?(?:outside|unrelated to) (?:the |this )?(?:question|request)\b",
            reason,
            re.I,
        ):
            # Contradictory provider verdicts require correction, never a local pass.
            raise ConditionCheckError(
                "VERIFIER_CONDITION_VERDICT_INVALID", "applicability_reason_mismatch", expected
            )
        witness_failure = ""
        if not isinstance(quote, str):
            witness_failure = "quote_not_string"
        elif status == "covered":
            if not quote.strip():
                witness_failure = "covered_quote_empty"
            elif quote not in draft.text:
                witness_failure = "quote_not_in_answer"
            elif not _has_condition_witness(expected["source_quote"], quote):
                witness_failure = "quote_does_not_address_condition"
            elif not set(expected["equivalent_evidence_ids"]).intersection(draft.citation_ids):
                witness_failure = "condition_source_not_cited"
        elif quote:
            witness_failure = "noncovered_quote_not_empty"
        if witness_failure:
            raise ConditionCheckError(
                "VERIFIER_CONDITION_WITNESS_INVALID", witness_failure, expected
            )
        assert isinstance(quote, str)
        # A verifier must not use a true fragment as a witness for an omitted conjunct.
        # Full semantic verification remains mandatory; these checks catch common
        # internally inconsistent provider verdicts rather than claiming equivalence.
        if (
            status == "covered"
            and expected.get("kind") == "coverage_limit"
            and not re.search(
                r"仅|部分|尚未|无法|不能确认|未覆盖|不完整|"
                r"\b(?:partial|incomplete|unconfirmed|cannot|not all)\b",
                quote,
                re.I,
            )
        ):
            status, quote, reason = "missing", "", "回答没有说明已知的图关系覆盖缺口"
        if status == "covered":
            source_quote = _local_constraint_text(expected["source_quote"])
            restriction = re.search(r"仅限|仅在|仅当|\bonly\b", source_quote, re.I)
            condition_body = source_quote[restriction.start() :] if restriction else source_quote
            parts = re.split(r"且|并且|以及|\band\b|[；;]", condition_body, flags=re.I)
            material = [re.split(r"提供|支持|允许", p)[0] for p in parts if _terms(p)]
            if (
                expected.get("kind") != "workflow_branch"
                and (len(material) > 1 or restriction)
                and any(not _terms(p).intersection(_terms(quote)) for p in material)
            ):
                status, quote = "missing", ""
                reason = "回答遗漏了原文的并列前提或执行分支"
            elif re.search(
                r"不得|禁止|不包括|不包含|不支持|不适用|不含|不在|除外|\b(?:not|never|cannot|except)\b",
                source_quote,
                re.I,
            ) and not re.search(
                r"不得|禁止|不|除外|排除|自费|付费|收费|\b(?:not|never|cannot|except|exclud\w*|paid)\b",
                quote,
                re.I,
            ):
                status, quote, reason = "missing", "", "回答未保留原文的否定限制或例外"
            elif expected.get("kind") == "workflow_branch":
                condition = str(expected["condition"])
                for field in ("source", "target"):
                    endpoint_terms = _terms(str(expected.get(field, "")))
                    if endpoint_terms and not endpoint_terms.intersection(_terms(quote)):
                        status, quote, reason = "missing", "", "回答未保留判断节点与对应执行对象"
                        break
                negative_branch = re.search(
                    r"分支[：:]\s*(?:否|失败|未通过|false|no|failure)(?:[；;]|$)", condition, re.I
                )
                if (
                    status == "covered"
                    and negative_branch
                    and not re.search(
                        r"未|不|否|失败|异常|\b(?:not|no|false|fail\w*)\b", quote, re.I
                    )
                ):
                    status, quote, reason = "missing", "", "回答遗漏了否定或失败分支的前提"
        if status == "not_applicable" and question:
            # Semantic applicability is about the particular rule being asked,
            # not lexical overlap with the product or policy family. E.g. a
            # warranty-duration lookup does not ask for application materials.
            # Retain the high-confidence contradiction check: absence from the
            # answer alone can never justify declaring a rule irrelevant.
            omission_only = bool(
                re.search(
                    r"未在(?:回答|答案).*提及|(?:回答|答案).*(?:未提及|未涉及|没有提到)|"
                    r"not (?:mentioned|addressed|included) in (?:the )?answer|"
                    r"(?:the )?answer does not (?:mention|address|include)",
                    reason,
                    re.I,
                )
            ) and not re.search(
                r"问题|提问|询问|问的是|\b(?:question|request|asks?)\b", reason, re.I
            )
            overview = bool(
                re.search(
                    r"总结|概述|整个流程|全部步骤|\b(?:summari[sz]e|overview)\b", question, re.I
                )
            )
            identity_only = bool(
                re.search(r"(?:名字|名称|叫什么)|\b(?:name|called)\b", question, re.I)
            ) and not re.search(
                r"条件|限制|维修|流程|分支|\b(?:condition|repair|branch|limit)\b", question, re.I
            )
            if not identity_only and (
                omission_only
                or (overview and expected.get("kind") in {"workflow_branch", "coverage_limit"})
                or (
                    expected.get("kind") == "coverage_limit"
                    and re.search(
                        r"流程|路径|分支|架构|连接|\b(?:path|flow|branch|architecture|connect)\b",
                        question,
                        re.I,
                    )
                )
            ):
                status, reason = (
                    "missing",
                    "该条件与问题明确涉及的主题或流程有关，不能仅以未回答而排除",
                )
        checks.append(
            {
                "id": expected["id"],
                "evidence_id": expected["evidence_id"],
                "source_quote": expected["source_quote"],
                "status": status,
                "answer_quote": quote,
                "reason": reason[:500],
            }
        )
    return tuple(checks)


def condition_report(checks: tuple[dict[str, str], ...]) -> dict[str, Any]:
    # Public history contains counts, not stale source excerpts after revocation.
    return {
        "checked": len(checks),
        "covered": sum(c["status"] == "covered" for c in checks),
        "not_applicable": sum(c["status"] == "not_applicable" for c in checks),
        "missing": sum(c["status"] == "missing" for c in checks),
        "complete": all(c["status"] != "missing" for c in checks),
    }
