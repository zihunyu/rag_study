"""Validate config/.env without displaying any values."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import build_env_report, load_env  # noqa: E402

CONFIG_HINTS = {
    "RETRIEVAL_ACTIVE_GENERATION_ID": "填写已完成 MySQL/Zilliz 对账并发布的 generation ID",
    "TOKENIZER_ARTIFACT_PATH": "填写与 EMBEDDING_MODEL 匹配的 tokenizer.json 本地路径",
    "TOKENIZER_ARTIFACT_SHA256": "填写 Get-FileHash <tokenizer.json> -Algorithm SHA256 的结果",
    "TOKENIZER_ID": "填写供应商模型名和 tokenizer 版本组成的稳定标识",
    "VERIFIER_BASE_URL": "填写独立核验模型的 OpenAI-compatible Base URL",
    "VERIFIER_API_KEY": "填写核验模型服务商发放的真实 API Key",
    "VERIFIER_MODEL": "填写与 LLM_MODEL 不同的核验模型 ID",
    "PROVIDER_PRICING": "把六项 *_COST_PER_MILLION_CNY 填为服务商真实人民币单价",
    "OIDC_ISSUER_URL": "填写身份平台 Discovery 文档中的 issuer URL",
    "OIDC_AUDIENCE": "填写 Access Token 中预期的 aud 值",
    "OIDC_CLIENT_ID": "填写身份平台为 Backend/API 注册的 Client ID",
    "OIDC_CLIENT_SECRET": "填写该 Backend/API Client 的真实 Secret",
    "OIDC_TENANT_ID": "填写必须与 Token tenant claim 匹配的租户 ID",
    "OIDC_DEFAULT_SPACE_ID": "填写该租户默认知识空间 ID",
}


def _hint(key: str) -> str:
    return CONFIG_HINTS.get(key, "查看 config/.env.example 中同名变量旁的说明")


def _table(report: dict[str, object]) -> str:
    lines = [
        f"Requested Gate: {report['requested_gate']}",
        "VARIABLE | CONFIGURED | SOURCE | SECRET | TYPE",
    ]
    for item in report["variables"]:  # type: ignore[union-attr]
        lines.append("{name} | {configured} | {source} | {secret} | {type}".format(**item))
    lines.append("ISSUE_KEY | CODE | BLOCKING_GATE | ACTION")
    for item in report["issues"]:  # type: ignore[union-attr]
        lines.append(
            "{key} | {code} | {blocking_gate} | {hint}".format(**item, hint=_hint(str(item["key"])))
        )
    lines.append("SUMMARY | " + json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    lines.append(str(report["safe_output_contract"]))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check typed config/.env readiness")
    parser.add_argument("--gate", choices=[f"G{i}" for i in range(7)], default="G0")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--allow-blocked", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_env_report(load_env(ROOT), args.gate)
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.format == "json"
        else _table(report)
    )
    print(rendered)
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    ready = bool(report["summary"]["gate_ready"])  # type: ignore[index]
    return 0 if ready or args.allow_blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
