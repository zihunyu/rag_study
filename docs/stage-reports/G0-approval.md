# G0 审核批准

结论：`APPROVED:G0`

批准日期：2026-08-31

## 独立验证

- `.venv` Python 3.12.13；
- 配置 `gate_ready=true`，G0 schema/decision/gate/user blocker 均为 0；
- pytest：33 passed；
- Ruff lint/format：通过；
- mypy strict：23 个源文件、0 issues；
- npm check、后端/Worker/MinerU/迁移原生入口：通过；
- 五类 Harness 自身断言：通过，且真实 acceptance 保持 false/BLOCKED；
- 无 Docker全仓扫描：通过；
- 24 周/12 Sprint/270 人周、R1 全格式、G1/G2 样本门、Milvus G2 真实门和本地持久队列 ADR：一致。

## G1 批准范围

- WBS-10 工程基础；
- WBS-20 知识模型与基础入库；
- WBS-25 中仅表格结构化适配部分；音频/ASR 留在 G2；
- Python 本地持久队列、CanonicalDocument/Locator/Chunk v1、上传隔离、幂等、状态机、OpenAPI v1、本地文件安全；
- 文本 PDF、扫描/图片、DOCX、PPTX、表格各 10 份真实难例的 G1 验收。

禁止提前进入真实 Milvus/音频/问答生成、权限生命周期或 G2 之后的 WBS。

