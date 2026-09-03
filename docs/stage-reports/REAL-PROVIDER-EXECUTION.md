# 真实供应商执行结果

状态：`REVIEW_REQUESTED_REAL_MINERU_DOCX_PDF_V1_RESULT_COMPLETED`

本次严格按分阶段批准执行：旧 MinerU 与旧 Embedding attempt 各在首次请求后停止并保留
`UNKNOWN_OUTCOME`；供应商契约修正后，用户批准独立 Embedding v2 attempt，现已 67/67 批
成功完成。MinerU 扫描 v2 与 DOCX v1 只完成本地实现和测试，本轮新增 MinerU 调用 0。
`real_acceptance=false`，禁止范围未扩大。

## 执行前安全预检

- 扫描 PDF / 图片仍为 10/10 份授权快照；
- locator-validated Embedding 快照仍为 669 chunks；
- 两个 provider checkpoint 均不存在历史执行记录或未知状态；
- MinerU Token、供应商处理区域、Embedding 配置和 HTTPS Endpoint 仅检查 configured/status，
  未输出任何值；
- DOCX、Reranker、LLM、Zilliz 写入均未授权且未执行。

## 流 A：MinerU 扫描/图片

结果：`BLOCKED_MINERU_CREATE_BATCH_HTTP_ERROR_MANUAL_RECONCILIATION_REQUIRED`

- 真实低层请求：1（首次 create batch）；
- signed upload：0；status poll：0；ZIP download：0；
- completed files：0/10；本地匿名 artifact/ZIP/normalized nodes：0；
- checkpoint：1 条 `UNKNOWN_OUTCOME`，安全错误码
  `MINERU_CREATE_BATCH_HTTP_ERROR`；
- automatic retries：0；Token failover/resubmit：0；
- 当前执行版本未把安全 HTTP 数字状态持久化，不能在不重发请求的情况下进一步区分具体
  client error。修订后已增加安全状态记录，但本次不重跑；
- checkpoint 未删除或修改为成功；后续必须先由供应商侧确认未产生 batch/job，并核对
  Precision API 权限、Token 与 Endpoint 配置，再取得新的明确重试授权。

## 流 B：Embedding 669 chunks

结果：`BLOCKED_EMBEDDING_HTTP_400_UNKNOWN_OUTCOME_RECONCILIATION_REQUIRED`

- 脚本在创建执行器时引用不存在的 timeout 配置属性，真实请求前立即停止；
- 审核确认原 21 批预算未消耗后，批准修复入口仅执行一次；第 1 批收到 HTTP 400 后立即停止；
- real embedding requests：1；attempted batches：1/21；completed batches：0/21；vectors：0；
- checkpoint：1 条 `UNKNOWN_OUTCOME`，错误码 `EMBEDDING_HTTP_ERROR`，安全 HTTP 状态 400；
- automatic retries：0；Zilliz writes：0；
- 本地缺陷已修复为执行器既有 120 秒默认 timeout，并通过测试；这不是用户漏填配置；
- 669-chunk 快照复核未变，未加入任何 MinerU 新 chunk；
- manifest 固定 669 chunks / 21 batches；首批 chunk-ID 映射与快照一致；0 个已存向量，
  因此没有可声明成功的维度/finite 向量；
- checkpoint 不含正文、API Key、Base URL 或 URL；
- HTTP 400 后未重跑；后续本地契约核对确认 batch size 32 超过 provider 单请求 10 文本
  上限。配置改为 10、使用独立新 attempt 且取得新的明确授权后才能再次执行。

## 本地供应商契约修正（0 新调用）

- MinerU 继续使用官方 Precision API v4；现有 create batch 请求结构、Bearer 原始 Token、
  signed upload 与 batch result 查询协议保持不变；官方文档明确支持 DOCX/XLSX，但两者
  未获本次真实执行授权；
- 未来 MinerU 完整 4xx 或业务错误响应只持久化安全 HTTP status、顶层标量 code、
  安全错误类别与 trace ID SHA-256；不保存 msg/body/URL/Token；
- 未来 MinerU 完整 4xx/业务 code 记为确定 `FAILED`；timeout、transport、5xx 仍记为
  `UNKNOWN_OUTCOME`。旧 MinerU UNKNOWN checkpoint 不回填、不猜测、不修改；
- 已确认 Embedding 是 DashScope OpenAI-compatible `text-embedding-v4`、维度 1024；
  单请求最多 10 个文本，因此此前 batch size 32 是 HTTP 400 的确定根因；
- `config/.env.example` 已把 `EMBEDDING_BATCH_SIZE` 示例改为 10；用户的 `config/.env`
  未修改，当前必须由用户把这个非秘密键改为 10；
- 新计划固定 669 chunks、batch size 10、required batches 67、automatic retries 0、
  Zilliz write false、`approved=false`；
- 未来 attempt 使用 `provider-checkpoints/embedding-attempt-v2.json`，引用并保留旧失败
  `embedding.json`；计划阶段没有创建新 checkpoint；
- 新执行入口在正文读取、网络请求和 checkpoint 写入前校验 DashScope provider constraint；
  当前 batch size 32 会以安全配置问题名 fail-fast；
- 未来 Embedding 非 2xx 只保存 HTTP status 与响应中可用的标量 error code/type、trace ID
  hash；禁止保存 message/body/input/URL/API Key；
- 两个旧失败 checkpoint 经 SHA-256 复核均字节级不变；本地契约修正阶段新增真实网络调用 0。

## 流 C：Embedding v2 独立 attempt

结果：`COMPLETED_EMBEDDING_V2_669_CHUNKS_67_BATCHES`

- 执行前确认 `EMBEDDING_BATCH_SIZE=10`、669-chunk 快照未变、新 checkpoint 不存在、
  两个旧 checkpoint SHA-256 不变；
- 使用独立 `embedding-attempt-v2.json`，未复用、删除或覆盖旧 `embedding.json`；
- real requests：67；completed batches：67/67；vectors：669；
- 全部向量 1024 维且 finite；chunk-ID/index 顺序与快照映射一致；
- automatic retries：0；Zilliz writes：0；第二次 v2 execution：false；
- checkpoint 敏感字段命中 0，不含正文、API Key、Base URL、URL、message/body/input；
- 两个旧失败 checkpoint 字节级保持不变，MinerU 仍为旧 create 1 次、其他请求 0。

## MinerU 固定新 attempts（本轮仅计划与测试）

- `mineru-scan-attempt-v2.json`：仅 `pdf_scanned_or_image` 10 份重试；
  expected locator 10；
- `mineru-docx-attempt-v1.json`：仅 DOCX 10 份首次执行，匿名 `.docx` 上传；
  expected locator 20；
- 两流均固定 max files 10、max requests 330、poll 30、interval 10 秒、retry 0、
  Token failover/resubmit 0；
- 两个新 checkpoint 固定且与旧 `mineru.json` 隔离；执行前验证旧 hash、源快照、egress、
  用户授权和新 checkpoint 为空；
- 结果分别匿名原子持久化并从落盘节点严格复核一基 page locator；新增 chunks 不进入
  已完成的 669-chunk Embedding v2；
- 用户已批准两个范围，但本轮审核前 MinerU 新调用仍为 0，两个新 checkpoint 均未创建。

## 流 D：MinerU 扫描 v2 真实结果

结果：`FAILED_MINERU_SCAN_V2_CREATE_HTTP_401_DOCX_NOT_STARTED`

- 执行前旧 `mineru.json` hash、扫描/DOCX 源快照、egress 与 configured 状态均通过；
  两个新 MinerU checkpoint 均不存在；
- 仅启动一次 `mineru-scan-attempt:v2`；首次 create batch 返回 HTTP 401 后立即停止；
- request：1；completed files：0/10；upload/poll/download/artifact：0；
- checkpoint：1 条确定 `FAILED`，安全错误码 `MINERU_CREATE_BATCH_HTTP_ERROR`，HTTP 401；
- provider 标量 code/category 不存在；trace ID 仅保存 SHA-256，trace hash count 1；
- automatic retries：0；Token failover/resubmit：0；第二次扫描执行：false；
- checkpoint 敏感值模式 0，不含 URL、Token、原文件名、msg/body 或正文；
- 因扫描未通过 create/auth 阶段，按批准条件未启动 DOCX；DOCX checkpoint 不存在、request 0；
- 旧 `mineru.json` 与 Embedding v2 checkpoint SHA-256 均保持不变；
- 当前需要用户在供应商侧确认 Token 有效、未过期且具有官方 Precision API 权限；修正后
  仍需新的 attempt 与明确重试授权，不得复用本次 FAILED checkpoint。

## MinerU scan v3 本地准备（0 新调用）

- 用户已更新 `MINERU_TOKENS`；只读验证结果为 1 个 Token、无 `Bearer` 前缀、无首尾或
  内部空白、官方 v4 配置有效；未输出或持久化 Token 或其 hash；
- 新固定入口 `execute-scan-v3`、attempt revision `mineru-scan-attempt:v3`、checkpoint
  `mineru-scan-attempt-v3.json`；当前 checkpoint 不存在；
- scope 保持扫描/图片 10 份、expected locators 10、is_ocr true、max files 10、
  max requests 330、poll 30、interval 10 秒、retry/failover 0；
- 执行前同时验证旧 `mineru.json` 与 scan-v2 checkpoint SHA-256、源快照、egress、
  approved flag 和 scan-v3 checkpoint 不存在；
- scan-v3 计划 `approved_by_user=false`，Token 更新不视为真实重试授权；
- DOCX v1 固定入口及既有授权保留，但 scan-v3 10/10 完成前会 fail-fast，不能运行；
- 测试覆盖 v3 checkpoint/预算隔离、两个旧 hash 字节级不变、无正文/URL/Token 泄漏；
- 本地准备新增真实网络调用 0；当前真实计数仍为 scan-v2 request 1/HTTP401 failed，
  DOCX 0，Embedding v2 67/67 completed。

## 流 E：MinerU scan v3 真实结果

结果：`FAILED_SCAN_V3_TASK_AFTER_UPLOAD_DOCX_NOT_STARTED`

- scan-v3 执行前双旧 hash、源快照、egress、Token 格式与新 checkpoint 不存在均通过；
- 仅执行一次：create 1、signed upload 1、status poll 1，共 3 requests；
- create 与上传成功，首次任务状态查询返回失败/未知任务状态，安全错误码
  `MINERU_TASK_FAILED_OR_UNKNOWN`；
- checkpoint 为确定 `FAILED` 1 条；completed/UNKNOWN 0；HTTP status/provider code/category/
  trace hash 均无可用值；
- download/artifact/node/chunk/locator 均为 0；automatic retries/failover/resubmit 0；
  第二次 scan-v3 execution false；
- checkpoint 不含 URL、Token、原文件名、msg/body 或正文；
- 因 scan-v3 未达到 completed files 10/10 与 locators 10/10，DOCX v1 未启动，
  request 0、checkpoint 不存在；
- 旧 `mineru.json`、scan-v2 与 Embedding v2 checkpoint SHA-256 均保持不变；
- 后续需在供应商任务侧对账该匿名 batch 的处理失败原因；不得复用或重跑本次 v3。

## MinerU 状态机修正与 scan-v4（0 新调用）

- 官方中间状态集合扩展为 `waiting-file / uploading / pending / running / processing /
  converting`；这些状态会按 10 秒间隔继续轮询，不再误判失败；
- 成功状态为 `done / completed / success`；明确失败仅为 `failed / error / canceled /
  cancelled`，保存安全 provider state 与标量 err_code，禁止保存 err_msg；
- 未知新状态以 `MINERU_TASK_STATE_UNSUPPORTED` 停止，checkpoint 保存受限 ASCII state，
  使用 `UNSUPPORTED_PROVIDER_STATE`，不冒充 provider failed；
- 新固定入口 `execute-scan-v4`、attempt `mineru-scan-attempt:v4`、checkpoint
  `mineru-scan-attempt-v4.json`；planned true、approved false、executed false；
- v4 执行前验证旧 `mineru.json`、scan-v2、scan-v3 三个 SHA-256、源快照、egress、
  approved flag 与 v4 checkpoint 不存在；旧 v3 `FAILED` 字节级不变；
- DOCX v1 保留既有授权，但 scan-v4 10/10 与 locator 10/10 成功前会 fail-fast；
- fake-clock 测试覆盖 `waiting-file→converting→running→done` 及每次 sleep；明确 failed
  与未知状态均安全停止，err_msg/正文/URL/Token 不进入 checkpoint；
- 本地修正新增网络调用 0；真实计数保持 scan-v3 create/upload/poll 共 3 requests、
  completed 0、retry 0，DOCX 0。

## 流 F：MinerU scan-v4 真实结果

结果：`PARTIAL_SCAN_V4_4_COMPLETED_1_FAILED_CODE_-60002_DOCX_NOT_STARTED`

- 仅执行一次 scan-v4；未启动第二个进程、未重跑；
- requests 23 = create 5 + signed upload 4 + poll 10 + download 4；
- completed files 4、failed files 1、UNKNOWN 0；automatic retries/failover 0；
- 4 个完成文件均已匿名原子落盘：artifact 4、nodes 75、new chunks 75、locators 75、
  artifact bytes 888,634；
- 已完成文件 expected locators 4/4，matched locators 4/4；
- 第 5 个文件在 create batch 完整业务响应中返回标量 code `-60002`，安全类别
  `PROVIDER_BUSINESS_ERROR_UNCLASSIFIED`，无 HTTP status、无 trace hash；checkpoint 确定
  `FAILED`，未上传该文件；
- checkpoint 敏感值模式 0，不含 URL、Token、原文件名、msg/body 或正文；
- scan-v4 未达到 10/10 与 locator 10/10，按前置条件未启动 DOCX；DOCX request 0、
  checkpoint 不存在；
- 三个旧 MinerU checkpoint 与 Embedding v2 SHA-256 均保持不变；本轮
  Embedding/Reranker/LLM/Zilliz requests/writes 0；
- 新增 75 chunks 不加入已完成的 669-chunk Embedding v2；
- 后续需确认官方业务码 `-60002` 的供应商侧原因，并创建新的固定 attempt；不得复用或
  重跑本次 scan-v4。

## TIFF 本地派生与 scan-v5（0 新调用）

- 独立确认 position 5 为单帧 TIFF，官方 Precision 不支持 TIFF，`-60002` 属于正确拒绝；
- 新增 `single-frame-tiff-to-png:v1`：只接受 TIFF magic 与单帧图像，按原模式无损、
  确定性输出 PNG；多帧、非 TIFF 或不支持无损 PNG 的模式 fail-fast；
- 原 TIFF 始终只读并在转换前后复核 SHA-256；派生目录仅由匿名 sample ID、源 SHA 与
  converter revision 派生，固定 `input.png`/`manifest.json`，原子替换且失败无半成品；
- 匿名 manifest 仅含 source/derived SHA、converter revision、width 1489、height 2105、
  mode L、frame count 1 与字节数；不含原文件名、路径、正文或 Token；
- 派生 PNG 已在受控 `LOCAL_STORAGE_TEMP_DIR` 子目录生成并通过像素无损对比；原样本未修改；
- 新固定入口 `execute-scan-v5`、checkpoint `mineru-scan-attempt-v5.json`；仅选择 positions
  5–10 共 6 份，第 5 份使用派生 PNG，其余使用原输入；v4 已完成 4 份绝不重传；
- v5 固定 max files 6、max requests 198、expected locators 6、retry/failover 0；执行前验证
  mineru/scan-v2/v3/v4 四个旧 hash、v4 恰为 4 completed + 1 failed code -60002、派生
  manifest/hash、剩余源快照、egress、approved flag 与 v5 checkpoint 不存在；
- v5 完成 6/6 后会组合 v4 四份与 v5 六份的落盘节点，严格复核总 locator 10/10；新增
  chunks 只汇总，不加入已完成的 669-chunk Embedding v2；
- DOCX v1 改为等待 combined scan 10/10；v5 planned true、approved false、executed false；
- 本地测试覆盖无损/确定性、源不变、匿名路径、原子失败、多帧/非 TIFF 拒绝、v5 仅 6 份、
  completed 4 不重发、组合 locator 10 与旧 hash 不变；本地准备新增网络调用 0。

## 流 G：MinerU scan-v5 真实结果

结果：`COMPLETED_SCAN_V5_AND_COMBINED_SCAN_10_OF_10`

- scan-v5 仅执行一次，仅 positions 5–10 六份；v4 已完成四份未重传；
- requests 33 = create 6 + upload 6 + poll 15 + download 6；
- completed 6/6、failed 0、UNKNOWN 0；artifact 6、nodes/chunks/locators 82；
- v5 expected/matched locator 6/6；automatic retries/failover/resubmit 0；
- v4 + v5 combined：completed 10/10、expected/matched locator 10/10、artifact 10、
  nodes/chunks 157；combined Gate 通过；
- 派生 PNG manifest/hash 与像素复核通过，原 TIFF SHA-256 未变；v5 checkpoint 敏感模式 0；
- 五个既有 MinerU checkpoint 与 Embedding v2 SHA-256 均保持不变；第二次 v5 execution false；
- 新增 157 chunks 不加入已完成的 669-chunk Embedding v2。

## 流 H：MinerU DOCX v1 真实结果

结果：`FAILED_DOCX_CONTENT_LOCATOR_INVALID_NO_RETRY`

- combined scan Gate 通过后，仅启动一次既有授权 DOCX v1；
- requests 5 = create 1 + upload 1 + poll 2 + download 1；
- provider 处理和 ZIP 下载完成，但 content locator 不满足当前一基 page/bbox 合同，错误码
  `MINERU_CONTENT_LOCATOR_INVALID`；
- completed 0、FAILED 1、UNKNOWN 0；artifact/node/chunk/locator 0；
  automatic retries/failover/resubmit 0；
- 无 HTTP status/provider code/category/trace；checkpoint 敏感模式 0，不含 URL、Token、
  原文件名、msg/body 或正文；
- 五个既有 MinerU checkpoint、Embedding v2 和原 TIFF 均保持不变；第二次 DOCX execution
  false；
- DOCX 不得重跑；需先本地修正 Office content-list 无 page/bbox 时的页映射/locator 契约，
  并创建新的固定 attempt 与单独授权。

## DOCX locator 修正、recovery-v1 与 v2（0 新调用）

- 规范化 locator 改为 scope policy：扫描/图片仍强制 `page_idx + bbox`；DOCX/Office
  强制 `page_idx` 但 bbox 可选，有 bbox 时严格验证并保留，无 bbox 时 locator 仅 `{page}`，
  page 始终一基；
- Office 支持官方 `index` type；既有 text/list/table/image/chart/equation/header/footer/
  page_footnote 继续支持，未知类型仍 fail-fast；
- 固定 `recover-docx-v1`：只读原 v1 `FAILED/MINERU_CONTENT_LOCATOR_INVALID` checkpoint
  中的安全 batch ID；只允许 status query + download 既有结果，create/PUT 固定为 0；
- recovery 使用独立 `mineru-docx-recovery-v1.json`，原 v1 checkpoint 字节级只读；结果按
  新 Office locator policy 匿名原子落盘并严格复核首份 expected locators 2；
- 固定 `execute-docx-v2`：只处理原 DOCX positions 2–10 共 9 份，checkpoint
  `mineru-docx-attempt-v2.json`，max files 9、max requests 297、expected locators 18、retry 0；
  首份绝不重传；
- recovery 首份成功 + v2 九份完成后组合 DOCX completed 10/10 与 expected/matched locator
  20/20；新增 chunks 不加入 669；
- 测试覆盖 Office 无 bbox/page 合法、扫描无 bbox 仍拒绝、`index` type、recovery 无
  create/PUT、原 v1 hash 不变、v2 仅 9 份与组合 locator 20、无敏感泄漏；
- recovery-v1 与 DOCX v2 均 planned true、approved false、executed false；用户原 DOCX
  授权不自动扩展到恢复或新 attempt；本地修正新增网络调用 0；
- 当前真实计数保持 scan-v5 6/6、combined scan 10/10、DOCX v1 requests 5 / provider
  完成但本地 artifact 0、retry 0。

## 流 I：DOCX recovery-v1 真实结果与 v2 阻断

结果：`RECOVERY_ARTIFACT_COMPLETED_LOCATOR_1_OF_2_DOCX_V2_NOT_STARTED`

- recovery-v1 仅执行一次，严格复用 v1 既有 batch；create 0、PUT 0；
- requests 2 = status query 1 + download 1；completed provider artifact 1、FAILED/UNKNOWN 0；
- 匿名原子落盘 artifact 1、nodes/chunks 27、locators 27；automatic retries/failover 0；
- 首份 expected locator 2，但 matched locator 1，仅业务 locator Gate 未通过；
- recovery checkpoint 不含 URL、Token、文件名、msg/body 或正文；原 DOCX v1 checkpoint
  SHA-256 字节级不变；
- 因 locator 不是 2/2，按授权条件未启动 DOCX v2；v2 request 0、checkpoint 不存在；
- v2 前置门禁已补强为 recovery completed **且** locator 2/2；计划把 recovery 标记为
  `COMPLETED_PROVIDER_RESULT_LOCATOR_GATE_FAILED`，v2 标记为
  `BLOCKED_BY_DOCX_RECOVERY_PREREQUISITE`；
- scan combined 10/10、Embedding v2、五个既有 MinerU checkpoint 与原 TIFF 均保持不变；
  第二次 recovery false，Reranker/LLM/Zilliz requests/writes 0；
- 后续需重新设计 DOCX 页定位来源或业务验收标准；不得重跑 recovery-v1，也不得执行 v2。

## LibreOffice DOCX→PDF 与新 MinerU attempt（0 新调用）

- 用户已授权安装 LibreOffice 26.8.0.3；console launcher 与版本本地验证通过；
- 新增 `libreoffice-docx-to-pdf:v1`：只接受扩展名 `.docx`、ZIP magic、
  `[Content_Types].xml` 与 `word/document.xml` 结构和源 SHA 匹配的输入；
- 每份源文件先复制到受控匿名工作目录中的固定 `input.docx`，LibreOffice 不会看到原文件名；
- 转换命令固定使用 console launcher、`--headless --nologo --nodefault
  --nofirststartwizard`、独立匿名 `UserInstallation` profile、固定 `input.pdf` 输出；
- 进程通过本次创建的 Popen handle 管理；timeout 只 kill 该自有 handle，不枚举或终止其他
  LibreOffice/系统进程；stdout/stderr 捕获但不打印路径或正文；
- 输出验证 PDF magic、pypdf 可读且 page count > 0，转换前后源 DOCX SHA-256 不变；
- artifact 原子存入 `LOCAL_STORAGE_ARTIFACTS_DIR` 受控匿名子目录，固定 `input.pdf` 与
  `manifest.json`；manifest 仅含匿名 ID、source/derived SHA+bytes、page count、converter
  revision 与 LibreOffice 公共版本，不含原文件名、绝对路径或正文；
- 10 份授权 DOCX 均已本地转换成功：derived PDF 10、unique hash 10、page count min 2 / max 3 /
  total 25，10/10 覆盖 metadata expected 最大页；源快照全部保持；
- 新固定入口 `execute-docx-pdf-v1`、checkpoint `mineru-docx-pdf-attempt-v1.json`；只使用
  10 份派生 PDF，scope `docx_pdf`、is_ocr false、max files 10、max requests 330、poll 30、
  interval 10 秒、retry/failover 0、expected locators 20；
- DOCX PDF attempt 使用 PDF 严格 page+bbox locator；目标 completed 10/10、locator 20/20；
  新增 chunks 不加入 669；
- 执行前验证所有历史 MinerU checkpoint hash、10 份转换 manifest/hash/page coverage、egress、
  approved flag 与新 checkpoint 不存在；原生 DOCX content/recovery 证据全部保留；
- 测试覆盖匿名输入/输出、源不变、页数/hash、原子失败、timeout/owned process、转换失败无
  半成品、PDF magic、10 份 expected page coverage、计划 0 网络与旧 hash 不变；
- attempt `planned=true、approved_by_user=false、executed=false`；用户仅批准安装，不视为
  MinerU 新 attempt 授权；本地转换新增网络调用 0。

## 流 J：MinerU DOCX-PDF v1 真实结果

结果：`COMPLETED_DOCX_PDF_V1_10_OF_10_LOCATOR_20_OF_20`

- 仅执行一次 `mineru-docx-pdf-attempt:v1`，没有第二次 execution；
- requests 51 = create 10 + upload 10 + poll 21 + download 10；
- provider state `done` 10/10；completed files 10、FAILED/UNKNOWN 0；
- artifact 10、nodes/chunks/locators 302；expected/matched locator 20/20；
- automatic retries/failover/resubmit 0；无 HTTP status/provider code/category/trace error；
- checkpoint 敏感模式 0，不含 URL、Token、原文件名、msg/body 或正文；
- 10 份派生 PDF manifest/hash/page coverage、原 DOCX、全部历史 checkpoint、scan artifacts 与
  Embedding v2 SHA-256 均保持不变；
- Reranker/LLM/Zilliz requests/writes 0；新增 302 chunks 不加入已完成的 669-chunk
  Embedding v2；
- 原生 DOCX v1/recovery 作为 content-only 与失败分析证据继续保留；最终 DOCX 物理页码验收
  采用 LibreOffice PDF 严格 page+bbox 链路，格式 Gate 达到 10/10、locator 20/20。

## 完成后只读核对

| 证据 | MinerU旧 | scan v2 | scan v3 | scan v4 | scan v5 | DOCX v1 | recovery | DOCX-PDF | Embedding旧 | Embedding v2 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 外部请求 | 1 | 1 | 3 | 23 | 33 | 5 | 2 | 51 | 1 | 67 |
| 完成文件 / 批次 | 0 | 0 | 0 | 4 | 6 | 0 | 1 | 10 | 0 | 67 |
| 向量 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 669 |
| UNKNOWN_OUTCOME | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| FAILED | 0 | 1 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 |
| 自动重试 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Zilliz 写入 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

- 源样本准入仍为五类 50/50，其中本次扫描/图片范围 10/10；
- Embedding 快照仍为 669 chunks；
- secret scan：0 findings；未输出 Token、API Key、Base URL、签名 URL、原文件名或正文；
- DOCX recovery requests 2；DOCX v2、Reranker、LLM 请求均为 0；Docker 未使用；
- 当前完整质量门：274 passed、Ruff lint/format 258 files、mypy 100 source files、
  frontend test/build 与 secret scan 全部通过；
- 没有 Git commit、merge、rebase、tag、push 或 PR 操作。

STAGE_REVIEW_REQUESTED:REAL_MINERU_DOCX_PDF_V1_RESULT
