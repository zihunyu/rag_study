from __future__ import annotations

from pathlib import Path


def test_g3_frontend_exposes_qa_admin_feedback_and_retrieval_debug_contracts() -> None:
    root = Path(__file__).resolve().parents[2] / "frontend"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "src/App.vue").read_text(encoding="utf-8")
    api = (root / "src/api.js").read_text(encoding="utf-8")
    package = (root / "package.json").read_text(encoding="utf-8")
    for marker in (
        "知识问答",
        "创建知识库",
        "已入库文件",
        "查看分块",
        "分块状态",
        "从此知识库回答",
        "检索调试",
        "系统治理",
        "发布文档",
        "高级生命周期操作",
        "既有文档新版本",
        "检查质量并提交复核",
        "Pilot Go/No-Go 与灰度",
        "合成 UAT",
        "可选观察窗与最终报告",
    ):
        assert marker in script
    assert 'id="app"' in html
    for endpoint in ("/ask:stream", "/search", "/feedback", "/admin/audit-events"):
        assert endpoint in script + api
    assert '"vue"' in package and '"vite"' in package
    assert '"dev": "vite --host 127.0.0.1"' in package
    assert '"test:api": "node --test src/api.test.mjs src/fileHash.test.mjs"' in package
    assert '"test:unit": "vitest run src/App.test.js"' in package
    assert '"test:e2e": "playwright test"' in package
    assert "cleanup/local_file:run" in script
    assert "versions/upload-sessions" in script
    assert "If-Match" in script and "PROCESSING 不可发布" in script
    assert "quality-report" in script and "/review" in script
    assert "/admin/diagnostics" in script
    assert "/governance/pilots" in script
    assert "/governance/uat-cases" in script
    assert "final-acceptance-report" in script
    assert "真实证据缺失时必须保持 BLOCKED" in script
    assert "space_id: selectedSpaceId.value" in script
    assert "/document-versions/${item.version_id}/chunks" in script
    assert "cleanup/${store}:complete" not in script
    assert "说明卡片" not in script
