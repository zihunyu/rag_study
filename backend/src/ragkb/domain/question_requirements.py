"""Expand explicit parallel attributes and elliptical follow-ups without model calls."""

import re

_PAIRS = {
    "（": "）",
    "(": ")",
    "[": "]",
    "【": "】",
    "“": "”",
    "‘": "’",
    "「": "」",
    '"': '"',
    "《": "》",
}
_FIELDS = re.compile(
    r".{0,30}(?:优点|缺点|优势|劣势|适用人群|适用场景|适用对象|接口|续航|价格|费用|"
    r"期限|时长|材料|流程|步骤|条件|范围|方式|限制|参数|特点|用途|时间|地址|电话|精度|重量|尺寸)"
)


def split_unquoted(text: str, separators: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    for char in text:
        if stack and char == stack[-1]:
            stack.pop()
        elif char in _PAIRS:
            stack.append(_PAIRS[char])
        if not stack and char in separators:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def explicit_requirements(question: str) -> tuple[str, ...]:
    original = question.strip(" 。？?；;")
    if re.search(r"同时满足|同时具备|共同满足|均须|必须同时", original):
        return ()
    # Resolve a named follow-up against the previous predicate. All original conditions
    # remain in the full query used by retrieval/generation/verification.
    clauses = split_unquoted(original, "，,；;？?")
    if len(clauses) > 1:
        expanded = [clauses[0]]
        changed = False
        for clause in clauses[1:]:
            follow = re.fullmatch(r"(?:那么|那)?(.{1,60}?)呢", clause)
            if follow:
                predicate = re.fullmatch(
                    r"(?:请问|那么|那)?(.{1,80}?)(的.{1,100}|支持.{1,100}|能否.{1,100}|是否.{1,100}|"
                    r"需要.{1,100}|保修.{1,100}|续航.{1,100}|价格.{1,100}|有哪些.{1,100})",
                    expanded[-1],
                )
                if predicate:
                    expanded.extend(
                        name + predicate[2] for name in split_unquoted(follow[1], "和与、")
                    )
                else:
                    expanded.append(f"{follow[1]}（沿用前一项询问：{expanded[-1]}）")
                changed = True
            else:
                expanded.append(clause)
        if changed:
            return tuple(expanded)

    # An explicit list of attributes is a matrix when multiple named subjects are given.
    # Joint conditions such as '同时满足 A 和 B' are not attribute lists.
    subject, separator, attributes = original.rpartition("的")
    if not separator:
        subject, attributes = "", original
    clean = re.sub(r"(?:分别)?(?:是什么|有哪些|怎么样|如何)$", "", attributes)
    parts = split_unquoted(clean.replace("以及", "和"), "、和与及，,")
    if len(parts) < 2 or not all(_FIELDS.fullmatch(p) for p in parts):
        return ()
    subject = re.sub(r"^(?:请)?(?:给出|列出|说明|介绍|提供|查询|告诉我|比较|对比)", "", subject)
    subjects = split_unquoted(subject.replace("以及", "和"), "和与、") if subject else [""]
    return tuple(f"{name}的{part}" if name else part for name in subjects for part in parts)
