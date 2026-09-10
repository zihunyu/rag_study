"""Compose explicit section lists from source text; never certify their correctness."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ragkb.domain.rag import AtomicClaim, DraftAnswer, Evidence

REVISION = "source-list-compose-v4-continuation-guard"
SOURCE_LIST_INTRO = "按所引资料的章节编号逐条整理如下："
_ITEM = re.compile(r"^\s*(?:[（(](\d{1,2})[)）]\s*|(\d{1,2})[、.)．](?!\d)\s*|(\d{1,2})\s+)(\S.*)$")
_LIST_TOPIC = re.compile(
    r"原则|规则|步骤|流程|清单|要求|须知|注意事项|列表|checklist|steps|rules|principles", re.I
)


def _plain(value: str) -> str:
    return re.sub(r"[\W_的]+", "", unicodedata.normalize("NFKC", value)).casefold()


def _heading(value: str) -> str:
    leaf = re.split(r"\s/\s| > ", value)[-1]
    return _plain(re.sub(r"^\s*[#*\\\s]*\d*[.、\s]*", "", leaf))


def _continued_heading(value: str) -> str:
    leaf = re.split(r"\s/\s| > ", value)[-1]
    base = re.sub(r"\s*[（(](?:续|续页|续表|continued)[)）]\s*$", "", leaf, flags=re.I)
    return _heading(base) if base != leaf else ""


def _body_key(value: str) -> str:
    # Normalize typographic separators only. Numeric signs, decimal points,
    # comparison operators, words and entity spellings remain substantive.
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\s,，;；。]", "", value)
    return re.sub(r"(?<!\d)\.|\.(?!\d)", "", value)


def _extends(longer: str, shorter: str) -> bool:
    longer, shorter = _body_key(longer), _body_key(shorter)
    if not shorter or not longer.startswith(shorter):
        return False
    if len(longer) > len(shorter):
        boundary = shorter[-1] + longer[len(shorter)]
        if re.fullmatch(r"\d[\d.]|[a-zA-Z][a-zA-Z]", boundary):
            return False
    return True


def _requested_heading(question: str) -> str:
    # A specific item, comparison, situation or requested summary still needs
    # ordinary question interpretation. Only an exact section-list request fits.
    if re.search(
        r"简要|简述|简短|概览|摘要|第\s*[一二三四五六七八九十\d]|前\s*\d|brief|summar",
        question,
        re.I,
    ):
        return ""
    if not (_LIST_TOPIC.search(question) or re.search(r"列出|有哪些|list\b", question, re.I)):
        return ""
    value = question.strip().rstrip("？?。.!！")
    value = re.sub(r"^(?:请)?(?:完整|全部|详细)?(?:列出|说明|介绍|解释)?(?:一下)?", "", value)
    value = re.sub(r"(?:分别)?(?:是什么|有哪些|包括哪些|是哪些)$", "", value)
    return _plain(value)


def numbered_items(text: str) -> tuple[tuple[int, str], ...]:
    result: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = _ITEM.match(line)
        if match:
            number = int(next(v for v in match.groups()[:3] if v))
            result.append((number, match[4].strip()))
        elif result and line.strip():
            if line.lstrip().startswith(("#", "SECTION_PATH:", "DOCUMENT_TITLE:")):
                return ()
            number, body = result[-1]
            result[-1] = (number, body + "\n" + line.strip())
    return tuple(result)


def requests_source_list(question: str, evidence: tuple[Evidence, ...]) -> bool:
    requested = _requested_heading(question)
    return bool(requested) and any(
        e.authorized
        and e.current_version
        and requested
        in {
            _heading(str(e.locator.get("section_path") or e.locator.get("heading") or "")),
            _continued_heading(
                str(e.locator.get("section_path") or e.locator.get("heading") or "")
            ),
        }
        for e in evidence
    )


@dataclass(frozen=True)
class SourceListPlan:
    title: str
    draft: DraftAnswer

    def preserves_items(self, draft: DraftAnswer) -> bool:
        # A later repair may add conditions or citations, but must not drop,
        # reword or reorder source-list entries silently.
        actual = numbered_items(draft.text)
        expected = self.draft.claims
        return len(actual) == len(expected) and all(
            number == i and claim.text in body
            for i, ((number, body), claim) in enumerate(zip(actual, expected, strict=True), 1)
        )


def source_list_plan(question: str, evidence: tuple[Evidence, ...]) -> SourceListPlan | None:
    requested = _requested_heading(question)
    if not requested:
        return None
    matching = [
        e
        for e in evidence
        if e.authorized
        and e.current_version
        and not e.locator.get("visual_facts")
        and _heading(str(e.locator.get("section_path") or e.locator.get("heading") or ""))
        == requested
    ]
    parsed = [(e, numbered_items(e.display_text or e.text)) for e in matching]
    complete = [
        (e, items)
        for e, items in parsed
        if 2 <= len(items) <= 40 and [n for n, _ in items] == list(range(1, len(items) + 1))
    ]
    if not complete or len({(e.document_id, e.document_version_id) for e, _ in complete}) != 1:
        return None
    primary, base = max(complete, key=lambda pair: len(pair[1]))
    # A 1..N prefix is not a complete list when the authorized source also
    # contains later items. Let ordinary synthesis read the whole evidence pool
    # instead of projecting a partial prefix and making repair preserve it.
    bodies = {_body_key(body) for _, body in base}
    for item in evidence:
        if not item.authorized or not item.current_version or item.locator.get("visual_facts"):
            continue
        scope = str(item.locator.get("section_path") or item.locator.get("heading") or "")
        if requested not in {_heading(scope), _continued_heading(scope)}:
            continue
        if any(
            number > len(base) and _body_key(body) not in bodies
            for number, body in numbered_items(item.display_text or item.text)
        ):
            return None
    peers = [
        (e, items)
        for e, items in parsed
        if e.document_version_id == primary.document_version_id
        and (len(items) == 1 or (e, items) in complete)
    ]
    claims = []
    for number, _body in base:
        variants = [(e, text) for e, items in peers for n, text in items if n == number]
        # Only merge textually nested copies in the same document version.
        # Divergent lists, scopes or clauses fall back to normal synthesis and
        # conflict checking; numbering alone does not prove equivalence.
        _, longest = max(variants, key=lambda pair: len(_body_key(pair[1])))
        if not all(_extends(longest, text) for _, text in variants):
            return None
        # The same exact paragraph can have a different number in a repeated
        # source section. Attach its citation instead of appending it as a new
        # condition. Never merge requirements or remove their semantic checks.
        aliases = [
            e.evidence_id
            for e, items in parsed
            if e.document_id == primary.document_id
            and e.document_version_id == primary.document_version_id
            and any(_body_key(body) == _body_key(longest) for _, body in items)
        ]
        ids = tuple(dict.fromkeys([*(e.evidence_id for e, _ in variants), *aliases]))
        claims.append(AtomicClaim(longest, ids))
    text = (
        SOURCE_LIST_INTRO
        + "\n\n"
        + "\n\n".join(
            f"{i}. {claim.text} " + "".join(f"[{identity}]" for identity in claim.evidence_ids)
            for i, claim in enumerate(claims, 1)
        )
    )
    if len(text) > 12000:
        return None  # Never silently truncate the requested list to fit a budget.
    return SourceListPlan(
        requested,
        DraftAnswer(
            text,
            tuple(dict.fromkeys(i for c in claims for i in c.evidence_ids)),
            tuple(claims),
            synthesized=True,
        ),
    )
