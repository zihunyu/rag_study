"""Conservative numeric evidence checks; uncertain language needs semantic verification.

This is a bounded grammar, not a natural-language entailment engine. Values, units,
comparators and ranges are compared exactly, with local subject context retained.
Ambiguous colloquial numbers and paraphrased subjects are never silently accepted.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal

NumericCheck = Literal["supported", "mismatch", "uncertain"]
Relation = Literal["eq", "lt", "le", "gt", "ge", "range", "approx"]
NumericTextSignature = tuple[tuple[str, ...], tuple[tuple[tuple[Decimal, ...], str, Relation], ...]]
_ALIASES = str.maketrans(
    "〇○两兩壹贰貳叁參肆伍陆陸柒捌玖拾佰仟萬億點負",
    "零零二二一二二三三四五六六七八九十百千万亿点负",
)
_DIGITS = {char: n for n, char in enumerate("零一二三四五六七八九")}
_SMALL = {"十": 10, "百": 100, "千": 1000}
_CHARS = "零〇○一二两兩三四五六七八九十百千万亿壹贰貳叁參肆伍陆陸柒捌玖拾佰仟萬億"
_NUMBER = (
    rf"[+\-负負]?(?:\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?|"
    rf"[{_CHARS}]+(?:[点點][{_CHARS}\d]+)?)(?:[十百千万亿萬億]+)?"
)
_ENGLISH = dict(
    zip(
        (
            "zero one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
        ).split(),
        range(21),
        strict=True,
    )
)
_ENGLISH_PATTERN = re.compile(r"\b(?:" + "|".join(_ENGLISH) + r")\b", re.IGNORECASE)


def _integer(text: str) -> int:
    if text.isascii() and text.isdigit():
        return int(text)
    if all(char in _DIGITS for char in text):
        if len(text) == 2 and "零" not in text:
            raise ValueError("ambiguous adjacent digits")
        return int("".join(str(_DIGITS[char]) for char in text))
    for unit, multiplier in (("亿", 100_000_000), ("万", 10_000)):
        if unit in text:
            if text.count(unit) != 1:
                raise ValueError("repeated magnitude")
            left, right = text.split(unit)
            high = _integer(left)
            if not 0 < high < multiplier:
                raise ValueError("invalid magnitude")
            # 一万二 can mean 10002 or 12000: require explicit place values.
            if right and not right.startswith("零") and all(c in _DIGITS for c in right):
                raise ValueError("abbreviated magnitude")
            low = _integer(right) if right else 0
            if low >= multiplier:
                raise ValueError("unordered magnitude")
            return high * multiplier + low
    total, digit, last_unit = 0, None, 10_000
    zero_seen = False
    for char in text:
        if char in _DIGITS:
            value = _DIGITS[char]
            if value == 0:
                if digit is not None:
                    raise ValueError("invalid zero")
                zero_seen = True
            else:
                if digit is not None:
                    raise ValueError("missing place value")
                digit = value
        elif char in _SMALL:
            small_unit = _SMALL[char]
            if small_unit >= last_unit or (digit is None and not (small_unit == 10 and total == 0)):
                raise ValueError("unordered place value")
            total += (digit if digit is not None else 1) * small_unit
            digit, last_unit, zero_seen = None, small_unit, False
        else:
            raise ValueError("unsupported number")
    if digit is not None:
        if last_unit > 10 and total and not zero_seen:
            raise ValueError("abbreviated place value")
        total += digit
    return total


def parse_number(text: str) -> Decimal:
    """Parse exact cardinals; raise ValueError for invalid or ambiguous forms."""
    text = unicodedata.normalize("NFKC", text).translate(_ALIASES).strip()
    if not text or len(text) > 64:
        raise ValueError("number length")
    sign = -1 if text.startswith(("-", "负")) else 1
    text = text.removeprefix("-").removeprefix("负").removeprefix("+")
    if "," in text:
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
            raise ValueError("invalid thousands separators")
        text = text.replace(",", "")
    with localcontext() as context:
        context.prec = 80
        arabic = re.fullmatch(r"(\d+(?:\.\d+)?)([十百千万亿]*)", text)
        if arabic:
            value = Decimal(arabic[1])
            magnitude = arabic[2]
            if magnitude:
                if magnitude not in ("十", "百", "千", "万", "亿", "千万", "百万", "十万", "万亿"):
                    raise ValueError("unsupported magnitude")
                multiplier = {"十": 10, "百": 100, "千": 1000, "万": 10_000, "亿": 100_000_000}
                for char in magnitude:
                    value *= multiplier[char]
            return sign * value
        if "点" in text:
            integer, fraction = text.split("点", 1)
            magnitude = ""
            if fraction.endswith(("万", "亿")):
                fraction, magnitude = fraction[:-1], fraction[-1]
            if not fraction or any(c not in _DIGITS and c not in "0123456789" for c in fraction):
                raise ValueError("invalid decimal")
            digits = "".join(str(_DIGITS[c]) if c in _DIGITS else c for c in fraction)
            value = Decimal(_integer(integer)) + Decimal("0." + digits)
            return sign * value * (10_000 if magnitude == "万" else 100_000_000 if magnitude else 1)
        return Decimal(sign * _integer(text))


def _prepare(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return _ENGLISH_PATTERN.sub(lambda m: str(_ENGLISH[m[0]]), text)


def normalize_numeric_text(text: str) -> str:
    """Compatibility/display normalization only; never used for evidence matching."""

    def convert(match: re.Match[str]) -> str:
        try:
            return format(parse_number(match[0]), "f")
        except ValueError:
            return match[0]

    normalized = re.sub(_NUMBER, convert, _prepare(text))
    for pattern, unit in ((r"\byears?\b", "年"), (r"\bmonths?\b", "月"), (r"\bdays?\b", "天")):
        normalized = re.sub(pattern, unit, normalized)
    return re.sub(r"\s+", "", normalized)


# Calendar years/months and business days deliberately have no duration conversion.
_UNITS: dict[str, tuple[str, int]] = {}
for _names, _canonical, _scale in (
    ("天 日 day days", "second", 86400),
    ("小时 时 hour hours", "second", 3600),
    ("分钟 minute minutes", "second", 60),
    ("秒 秒钟 second seconds", "second", 1),
    ("周 星期 week weeks", "second", 604800),
    ("年 year years", "year", 1),
    ("个月 月 month months", "month", 1),
    ("工作日 个工作日", "business_day", 1),
    ("元 人民币 人民币元 yuan cny rmb ¥ ￥", "CNY", 1),
    ("美元 美金 usd $", "USD", 1),
    ("港元 港币 hkd", "HKD", 1),
    ("欧元 eur €", "EUR", 1),
    ("% percent", "percent", 1),
    ("个百分点 percentage_points", "percentage_point", 1),
    ("kg 千克 公斤", "gram", 1000),
    ("g 克", "gram", 1),
    ("公里 千米 km", "meter", 1000),
    ("米 m", "meter", 1),
    ("个", "count", 1),
    ("次", "occurrence", 1),
):
    for _name in _names.split():
        _UNITS[_name] = (_canonical, _scale)
_UNIT_PATTERN = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
_SCALAR = re.compile(
    rf"(?P<prefix>百分之|人民币|港币|cny\s*|usd\s*|hkd\s*|[¥$€])?"
    rf"(?P<number>{_NUMBER})(?:\s*(?P<unit>{_UNIT_PATTERN}))?"
)
_IDENTIFIER = re.compile(r"(?<![a-z0-9])(?:[a-z]+[0-9][a-z0-9]*|[0-9]+[a-z][a-z0-9]*)(?![a-z0-9])")
_DATE = re.compile(
    rf"(?P<y>{_NUMBER})年\s*(?P<m>{_NUMBER})月\s*(?P<d>{_NUMBER})[日号]|"
    r"(?<![\d.])(?P<iso>\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(?!\d)"
)
_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)")
_RANGE = re.compile(r"\s*(?:至|到|[~～–—-]|to)\s*")
_CLAUSE = re.compile(r"[。；;！!?？\n]|(?<!\d)[，,]|[，,](?!\d)")
_NON_NUMERIC_WORDS = re.compile(
    r"统一|唯一|一致|一律|一般|一起|一样|一定|一旦|一些|进一步|万一|一如|百姓|大陆|陸地"
)
_PREFIX_RELATIONS: tuple[tuple[str, Relation], ...] = (
    (r"(?:不超过|不得超过|不高于|不大于|最多|至多|上限(?:为|是)?|<=|≤|at most)\s*$", "le"),
    (r"(?:不少于|不低于|不得少于|至少|下限(?:为|是)?|>=|≥|at least)\s*$", "ge"),
    (r"(?:超过|大于|高于|多于|>|more than|greater than)\s*$", "gt"),
    (r"(?:少于|低于|小于|不足|<|less than)\s*$", "lt"),
    (r"(?:约为|大约|约|左右|approximately|about)\s*$", "approx"),
)
_SUFFIX_RELATIONS: tuple[tuple[str, Relation], ...] = (
    (r"\s*(?:以内|以下|及以下|或以下|内)", "le"),
    (r"\s*(?:以上|及以上|或以上)", "ge"),
    (r"\s*(?:左右|上下)", "approx"),
)


@dataclass(frozen=True)
class NumericFact:
    values: tuple[Decimal, ...]
    unit: str
    relation: Relation
    subject: tuple[str, str]
    start: int
    end: int
    certain: bool = True

    @property
    def signature(self) -> tuple[tuple[Decimal, ...], str, Relation]:
        return self.values, self.unit, self.relation


@dataclass(frozen=True)
class ParsedFacts:
    facts: tuple[NumericFact, ...]
    uncertain: bool = False
    identifiers: tuple[str, ...] = ()


def numeric_text_signature(text: str) -> NumericTextSignature | None:
    """Conservative deduplication key: keep every non-numeric word and its order.

    Equal values alone are insufficient: the skeleton retains negation, objects,
    scope clauses and punctuation. Unknown numeric grammar cannot authorize merging.
    """
    parsed = extract_numeric_facts(text)
    if parsed.uncertain:
        return None
    prepared = _prepare(text)
    parts: list[str] = []
    cursor = 0
    for fact in parsed.facts:
        parts.append(prepared[cursor : fact.start])
        cursor = fact.end
    parts.append(prepared[cursor:])
    # Keep literal segments separate so a document containing a marker-like string
    # cannot collide with a different arrangement of actual numeric facts.
    skeleton = tuple(re.sub(r"\s+", " ", part).strip() for part in parts)
    # Numeric parsing casefolds input, but entity case can carry meaning (US/us).
    # Keep case outside recognized English number words in the merge signature.
    case_tokens = re.findall(
        r"[A-Z]+", _ENGLISH_PATTERN.sub("", unicodedata.normalize("NFKC", text))
    )
    return (repr(case_tokens), *skeleton), tuple(fact.signature for fact in parsed.facts)


def _subject(text: str) -> str:
    text = re.sub(
        r"^(?:根据已验证证据[，,]?|根据证据[，,]?|另外[，,]?|因此[，,]?|从|在)", "", text.strip()
    )
    text = re.sub(r"^(?:the\s+|(?:设备|产品)(?=保修|退款|有效期|使用期限))", "", text)
    text = re.sub(r"(?:为|是|等于|介于|之间|between|\bis\b|\bare\b|\bfrom\b)\s*$", "", text.strip())
    return re.sub(r"\s+", "", text).strip(":：,，。.；;!?！？")


def extract_numeric_facts(raw_text: str) -> ParsedFacts:
    text = _prepare(raw_text)
    found: list[NumericFact] = []
    occupied: list[tuple[int, int]] = []
    identifiers = tuple(
        match for match in _IDENTIFIER.finditer(text) if not _SCALAR.fullmatch(match[0])
    )
    occupied.extend(match.span() for match in identifiers)
    non_numeric = tuple(match.span() for match in _NON_NUMERIC_WORDS.finditer(text))
    uncertain = False

    def overlaps(start: int, end: int) -> bool:
        return any(start < right and end > left for left, right in occupied)

    # Recognize whole dates/times first so their components cannot support scalars.
    for pattern, kind in ((_DATE, "date"), (_TIME, "time")):
        for match in pattern.finditer(text):
            if overlaps(*match.span()):
                continue
            occupied.append(match.span())
            try:
                if kind == "date":
                    if match["iso"]:
                        year, month, day = (int(v) for v in re.split(r"[-/.]", match["iso"]))
                    else:
                        components = tuple(parse_number(match[k]) for k in ("y", "m", "d"))
                        if any(value != value.to_integral_value() for value in components):
                            raise ValueError("fractional date component")
                        year, month, day = (int(value) for value in components)
                    if not (1 <= year <= 9999 and 1 <= month <= 12 and 1 <= day <= 31):
                        raise ValueError("date component out of range")
                    value = Decimal(date(year, month, day).toordinal())
                else:
                    hour, minute, second = int(match[1]), int(match[2]), int(match[3] or 0)
                    if hour >= 24 or minute >= 60 or second >= 60:
                        raise ValueError("invalid time")
                    value = Decimal(hour * 3600 + minute * 60 + second)
                found.append(NumericFact((value,), kind, "eq", ("", ""), *match.span()))
            except (ValueError, InvalidOperation):
                uncertain = True
    for match in _SCALAR.finditer(text):
        start, end = match.span()
        if overlaps(start, end):
            continue
        if any(left <= start and end <= right for left, right in non_numeric):
            continue
        number, unit, prefix = match["number"], match["unit"], (match["prefix"] or "").strip()
        if number.startswith("-") and any(
            fact.end <= start and not text[fact.end : start].strip() for fact in found
        ):
            # In 3-5天 the hyphen is a range separator, not the endpoint's sign.
            start += 1
            number = number[1:]
        # Do not extract fragments of identifiers, or numerals embedded in words (统一).
        if start and re.match(r"[a-z0-9_]", text[start - 1]):
            uncertain = True
            continue
        if not unit and not prefix and not any(c.isascii() and c.isdigit() for c in number):
            if (start and text[start - 1] not in "为是:=≥≤<>至到从 \n，,。;；") or (
                end < len(text) and text[end].isalpha() and text[end] not in "至到"
            ):
                # Unrecognized quantities need semantics, while idioms like 统一
                # must not turn ordinary prose into an uncertain numeric claim.
                uncertain = True
                continue
        certain = not (end < len(text) and re.match(r"[a-z0-9_]", text[end]))
        if not certain:
            uncertain = True
        try:
            value = parse_number(number)
            canonical, scale = _UNITS.get(unit or "", ("number", 1))
            if prefix:
                prefix_unit = "percent" if prefix == "百分之" else _UNITS[prefix][0]
                if unit and canonical != prefix_unit:
                    raise ValueError("conflicting unit")
                canonical = prefix_unit
            with localcontext() as context:
                context.prec = 80
                value *= scale
            found.append(NumericFact((value,), canonical, "eq", ("", ""), start, end, certain))
        except (ValueError, InvalidOperation):
            uncertain = True
    found.sort(key=lambda fact: fact.start)
    merged: list[NumericFact] = []
    for fact in found:
        if merged and _RANGE.fullmatch(text[merged[-1].end : fact.start]):
            previous = merged.pop()
            # Unitless endpoints inherit the explicit endpoint's original unit/scale.
            left, right = previous.values[-1], fact.values[0]
            unit = previous.unit if fact.unit == "number" else fact.unit
            certain = previous.certain and fact.certain
            if previous.unit == "number" or fact.unit == "number":
                explicit = fact if previous.unit == "number" else previous
                token = _SCALAR.fullmatch(text[explicit.start : explicit.end])
                scale = _UNITS.get(token["unit"] or "", ("number", 1))[1] if token else 1
                implicit = previous if previous.unit == "number" else fact
                # Shared magnitudes (1至2万元) need semantic interpretation. Do not
                # guess whether the first endpoint inherits 万 as well as 元.
                if token and re.search(r"[十百千万亿萬億]", token["number"]):
                    implicit_token = _SCALAR.fullmatch(text[implicit.start : implicit.end])
                    if implicit_token and not re.search(
                        r"[十百千万亿萬億]", implicit_token["number"]
                    ):
                        certain = False
                with localcontext() as context:
                    context.prec = 80
                    if previous.unit == "number":
                        left *= scale
                    else:
                        right *= scale
            if (
                len(previous.values) != 1
                or (previous.unit != fact.unit and "number" not in (previous.unit, fact.unit))
                or left > right
            ):
                certain = False
            uncertain = uncertain or not certain
            merged.append(
                NumericFact(
                    (left, right), unit, "range", ("", ""), previous.start, fact.end, certain
                )
            )
        else:
            merged.append(fact)
    result: list[NumericFact] = []
    for index, fact in enumerate(merged):
        start_boundary = merged[index - 1].end if index else 0
        end_boundary = merged[index + 1].start if index + 1 < len(merged) else len(text)
        before = _CLAUSE.split(text[start_boundary : fact.start])[-1]
        after = _CLAUSE.split(text[fact.end : end_boundary])[0]
        relation = fact.relation
        certain = fact.certain
        for relation_pattern, candidate in _PREFIX_RELATIONS:
            relation_match = re.search(relation_pattern, before)
            if relation_match:
                before = before[: relation_match.start()]
                if relation != "eq":
                    certain = False
                relation = candidate
                break
        for relation_pattern, candidate in _SUFFIX_RELATIONS:
            relation_match = re.match(relation_pattern, after)
            if relation_match:
                after = after[relation_match.end() :]
                if relation != "eq" and relation != candidate:
                    certain = False
                relation = candidate
                break
        # Exclusivity, negation, open/compound ranges and approximations need semantics.
        if relation == "approx" or re.search(
            r"不含|不包括|不满|余|多|半|不为|不是|(?<!不)含|[()\[\]]", before + after
        ):
            certain = False
        uncertain = uncertain or not certain
        result.append(
            replace(
                fact,
                relation=relation,
                subject=(_subject(before), _subject(after)),
                certain=certain,
            )
        )
    # Common unsupported forms must not become a successful empty check.
    if re.search(
        r"半(?:个?月|年|天|小时)|百分之\s*(?![\d"
        + _CHARS
        + r"])|(?:\d|["
        + _CHARS
        + r"])(?:余|多|来)[年月日天元%]",
        text,
    ):
        uncertain = True
    return ParsedFacts(tuple(result), uncertain, tuple(match[0] for match in identifiers))


def check_numeric_facts(claim: str, sources: tuple[str, ...]) -> NumericCheck:
    expected = extract_numeric_facts(claim)
    observed = tuple(extract_numeric_facts(source) for source in sources)
    source_identifiers = {identifier for parsed in observed for identifier in parsed.identifiers}
    if any(identifier not in source_identifiers for identifier in expected.identifiers):
        return "mismatch"
    uncertain = expected.uncertain
    facts = tuple(fact for parsed in observed for fact in parsed.facts if fact.certain)
    for fact in expected.facts:
        if not fact.certain:
            continue
        # Anonymous values in lists/coordinates have no object binding. Treating
        # every such value as the same subject falsely contradicts valid tuples.
        # They still require semantic checking of order, labels and conditions.
        bound = tuple(
            candidate
            for candidate in facts
            if fact.subject != ("", "") and candidate.subject == fact.subject
        )
        if not bound and fact.subject[0]:
            qualified = tuple(
                candidate
                for candidate in facts
                if candidate.subject[1] == fact.subject[1]
                and candidate.subject[0].endswith("的" + fact.subject[0])
            )
            # 保修期 can refer to the sole explicit X的保修期 in its cited evidence.
            # Multiple named objects remain ambiguous, even if their values coincide.
            if len({candidate.subject for candidate in qualified}) == 1 and not any(
                parsed.uncertain for parsed in observed
            ):
                bound = qualified
        if bound:
            if any(candidate.signature != fact.signature for candidate in bound):
                return "mismatch"
        elif (
            fact.subject == ("", "")
            and len(expected.facts) == len(facts) == 1
            and not any(parsed.uncertain for parsed in observed)
            and facts[0].signature == fact.signature
        ):
            # A bare answer such as 三年 has one unambiguous numeric source. The
            # semantic verifier still checks its relationship to the question.
            continue
        elif any(candidate.signature == fact.signature for candidate in facts):
            # Same value with a different/omitted object is not proof of entailment.
            uncertain = True
        elif any(parsed.uncertain for parsed in observed):
            uncertain = True
        else:
            return "mismatch"
    return "uncertain" if uncertain else "supported"


def explicit_calculation_requires_review(claim: str, sources: tuple[str, ...]) -> bool:
    """Permit checked arithmetic to reach independent semantic verification.

    This does not establish object/condition/unit bindings. The semantic verifier
    still checks those against the cited sources and the complete answer. A bare
    novel number, an incorrect equation or an unavailable operand stays blocked.
    """
    text = _prepare(claim).replace("−", "-")
    number = r"[+-]?\d{1,18}(?:\.\d{1,8})?"
    unit = r"(?:kwh|kw|wh|w|v|a)"
    equation = re.compile(
        rf"(?<![\w.])(?P<a>{number})\s*(?P<au>{unit})?\s*(?P<op>[+*/×÷-])\s*"
        rf"(?P<b>{number})\s*(?P<bu>{unit})?\s*=\s*(?P<c>{number})(?![\d.])"
        rf"\s*(?P<cu>{unit})?(?![a-z])"
    )
    matches = tuple(equation.finditer(text))
    if not matches or len(matches) > 4:
        return False
    source_values = {
        value
        for source in sources
        for fact in extract_numeric_facts(source).facts
        if fact.certain and fact.relation == "eq"
        for value in fact.values
    }
    results = set()
    for match in matches:
        if match["au"] or match["bu"]:
            # Explicit operand units are supported only for same-unit addition or
            # subtraction. No dimensional conversion is inferred locally.
            if match["op"] not in {"+", "-"} or not (match["au"] == match["bu"] == match["cu"]):
                return False
        a, b, result = (Decimal(match[group]) for group in ("a", "b", "c"))
        if a not in source_values or b not in source_values:
            return False
        with localcontext() as context:
            context.prec = 80
            op = match["op"]
            if op in {"/", "÷"} and not b:
                return False
            actual = (
                a + b if op == "+" else a - b if op == "-" else a * b if op in {"*", "×"} else a / b
            )
        if actual != result:
            return False
        results.add(result)
    remainder = equation.sub("", text)
    # Check the rest normally after removing only the independently calculated
    # outputs; unrelated literal errors cannot hitchhike on a valid equation.
    parsed = extract_numeric_facts(remainder)
    for fact in reversed(parsed.facts):
        if fact.certain and len(fact.values) == 1 and fact.values[0] in results:
            remainder = remainder[: fact.start] + remainder[fact.end :]
    return check_numeric_facts(remainder, sources) != "mismatch"
