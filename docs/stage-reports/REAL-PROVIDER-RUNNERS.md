# 真实供应商执行器就绪报告

状态：`SUPERSEDED_BY_PROVIDER_CONTRACT_CORRECTION`

本修订只完成真实供应商执行器、离线计划和安全测试。真实 MinerU、Embedding、
Reranker、LLM 调用均为 0，Zilliz 写入为 0；`real_acceptance=false`。

## 配置预检

- `MINERU_TOKENS` 已配置且英文逗号列表格式有效；未读取或输出 Token 值；
- `AI_APPROVED_PROCESSING_REGIONS` 已配置；执行入口在读取正文前验证区域配置和数据分类；
- Zilliz Cloud Endpoint 为合法 HTTPS；Zilliz 与 Embedding 维度均为整数且一致；
- `config/.env` 未被修改、未进入版本控制、未被 secret scan 扫描或输出。

## MinerU Precision API 执行器

- 使用官方 batch 协议：创建批次、签名 URL 原始字节 PUT、批次状态轮询、结果 ZIP 下载；
- 标准 v4 配置的 DOC/DOCX/PPT/PPTX/XLSX/PDF/图片/HTML 能力由文档确认，能力确认请求数为 0；
- DOCX 真实 10 份仍未获单独提交授权，执行入口不会把本次扫描/图片授权扩展到 DOCX；
- 扫描 PDF / 图片预算固定为最多 10 份、每文件最多 30 次轮询、轮询间隔 10 秒、
  总请求硬上限 330、自动重试 0、Token 自动切换 0；
- 签名上传与下载 URL 只存在内存，不写 checkpoint；创建、上传、下载任一中断时，
  checkpoint 保持 `UNKNOWN_OUTCOME`，恢复时要求人工对账，禁止重复提交；
- 后续契约修正：完整 4xx/业务 code 属于确定失败并保存安全 status/code/trace hash；
  仅 timeout、transport、5xx 保持 `UNKNOWN_OUTCOME`；
- 签名 URL 强制 HTTPS，拒绝 userinfo、localhost、私网/保留 IP 与本地域名；
  下载限制重定向次数、逐跳重新验证、压缩包/展开大小、条目数和路径穿越；
- ZIP 使用唯一 `*_content_list.json`，兼容官方 content 类型并强制 page/bbox locator。

## 匿名本地结果与流水线交接

- `ResultStorePort` 位于 contracts；application 不依赖 infrastructure，执行脚本把实现组装到
  已有 `LOCAL_STORAGE_ARTIFACTS_DIR`；
- 安全验证后的完整供应商 ZIP、规范化节点 JSON 与 manifest 使用临时目录加原子替换落盘；
  任一文件写入失败不会留下可见的半成品 artifact；
- artifact 目录仅由 anonymous sample ID 与 ZIP SHA-256 派生，文件名固定，不含原文件名、
  URL、Token 或源路径；
- 规范化节点保留真实文本、表格、列表及其他受支持 content，包含 provider type、匿名 sample
  ID、bbox，并把 `page_idx` 转为一基 `page`；真实内容仅写本地受控 artifact；
- checkpoint 只保存匿名 artifact ID/相对引用、SHA-256、字节数和 node/chunk/locator 计数；
  artifact 原子持久化成功后才能进入 `COMPLETED`；
- 落盘失败或落盘成功但 checkpoint 完成前中断，均保持 `UNKNOWN_OUTCOME/PERSIST_RESULT`，
  禁止自动重发，可通过 artifact hash 人工对账；
- 执行脚本完成 10 份后重新读取落盘节点，对 expected locator 严格复核，只输出匿名汇总：
  成功文件数、locator expected/matched、新 chunk 数与 artifact hash 数；
- 新增扫描/图片 chunks 不进入当前已批准的 669-chunk Embedding 快照，需后续重新核定范围。

MinerU 输出格式依据：
[官方 Output Files 文档](https://github.com/opendatalab/MinerU/blob/master/docs/en/reference/output_files.md)。

## Embedding 执行器

- DashScope `text-embedding-v4` 输入快照仍固定为 locator-validated 669 chunks；provider
  batch size 上限 10，对应 67 batches；新 attempt `approved=false`、自动重试 0；
- 每批在请求前持久化 `UNKNOWN_OUTCOME` 与稳定 idempotency key；中断后禁止自动重发；
- 响应强制校验唯一且完整的 `index=0..n-1`，按 index 排序后再绑定 chunk ID；
- 校验向量数量、维度、有限数；checkpoint 保存 chunk ID、向量与哈希，不保存正文；
- 本阶段不执行 Zilliz 写入。

## UAT 候选

- 从授权 metadata/locator 本地生成 78 条 content-free 候选；模型和网络调用均为 0；
- 候选全部为 `PENDING_USER_REVIEW`；用户审核前 Reranker/LLM 硬阻断；
- ignored artifact：`artifacts/final-validation/uat-candidates/pending-review.json`。

## 验证证据

| 检查 | 结果 |
| --- | --- |
| pytest | PASS；248 passed，0 failed，0 skipped |
| MinerU/Embedding 定向协议、落盘与恢复测试 | PASS；33 passed |
| Ruff lint / format | PASS；235 files |
| mypy strict | PASS；99 source files |
| dependency boundaries | PASS；application 仅依赖 contracts 端口 |
| frontend test/build | PASS；3 tests + production build |
| config / secret scan | PASS；Gate ready，0 findings |
| provider plan / UAT generation | PASS；真实调用 0，Zilliz 写入 0 |

本阶段没有 Git commit、merge、rebase、tag、push 或 PR 操作。

STAGE_REVIEW_REQUESTED:REAL_PROVIDER_RUNNERS_READY_REV2
