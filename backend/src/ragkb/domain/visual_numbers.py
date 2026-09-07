"""Literal numeric witnesses for OCR; preserve unit case and Chinese boundaries."""

from __future__ import annotations

import re
import unicodedata

_UNITS = (
    "千瓦时",
    "毫安时",
    "千瓦",
    "毫瓦",
    "瓦特",
    "毫伏",
    "伏特",
    "毫安",
    "安培",
    "兆帕",
    "千帕",
    "帕斯卡",
    "摄氏度",
    "华氏度",
    "平方米",
    "立方米",
    "毫秒",
    "分钟",
    "小时",
    "千克",
    "毫克",
    "公斤",
    "厘米",
    "毫米",
    "公里",
    "个月",
    "kWh",
    "mAh",
    "kVA",
    "MPa",
    "kPa",
    "GHz",
    "MHz",
    "kHz",
    "MW",
    "kW",
    "mW",
    "Wh",
    "Ah",
    "kV",
    "mV",
    "mA",
    "μA",
    "μm",
    "ms",
    "μs",
    "ns",
    "Hz",
    "TB",
    "GB",
    "MB",
    "KB",
    "mm",
    "cm",
    "km",
    "kg",
    "mg",
    "mL",
    "ml",
    "°C",
    "°F",
    "rpm",
    "Pa",
    "W",
    "V",
    "A",
    "J",
    "N",
    "Ω",
    "L",
    "g",
    "m",
    "s",
    "%",
    "瓦",
    "伏",
    "安",
    "帕",
    "秒",
    "年",
    "月",
    "天",
    "次",
    "米",
    "克",
    "度",
)
_NUMBER = r"[+±−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+−-]?\d+)?"
_UNIT = "(?:" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + ")"
_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])"
    + _NUMBER
    + r"(?:\s*(?:±|[~～–—-]|至|到)\s*"
    + _NUMBER
    + r")?"
    + r"(?:\s*"
    + _UNIT
    + r"(?:[²³23]|/[A-Za-z]+[²³23]?)?(?![A-Za-z]))?"
)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).replace("−", "-")


def critical_tokens(text: str) -> list[str]:
    return [m.group() for m in _PATTERN.finditer(unicodedata.normalize("NFKC", text))]


def contains_number(text: str, token: str) -> bool:
    # Token equality prevents 23 matching 323, mW matching MW, or a range matching one endpoint.
    return compact(token) in {compact(t) for t in critical_tokens(text)}
