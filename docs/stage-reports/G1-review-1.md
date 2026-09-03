# G1 第一次审核结论

审核结果：`CHANGES_REQUIRED:G1`

提交策略：`NO_COMMITS`；禁止提交、合并、变基、标签、推送和 PR。

## 已通过

- Python 3.12 全质量门独立重跑：75 tests、Ruff lint/format、mypy strict、npm、OpenAPI、SQLite v2、原生入口、无 Docker和密钥扫描全部通过；0 failed、0 skipped。
- G1 配置 `gate_ready=true`，ASR 与真实样本已按用户要求推迟到 G4。
- CanonicalDocument/Locator/Chunk v1、上传隔离、SQLite 持久队列、OpenAPI 与配置迁移结构完整。

## 必须修正

1. `LocalIngestionWorker.run_once()` 对普通解析异常在记录队列失败后重新抛出；`run_worker()` 主循环没有捕获，单份坏文档会终止整个 Worker。主循环必须对单任务异常做安全日志/计数后继续轮询，同时保留 `--once` 可返回非零用于诊断；不得输出文档正文或密钥。增加“一个任务失败后下一个任务仍会执行”的回归测试。
2. 任务取消与 DocumentVersion 状态未同步：
   - QUEUED/RETRY_WAIT 取消后 Job 已 `CANCELLED`，Version 仍可能永久 `PROCESSING`；
   - RUNNING 取消只变成 `CANCEL_REQUESTED`，Worker 忽略 heartbeat 返回的取消标记，仍可能保存产物并完成成功。
   必须增加 Version `CANCELLED`（或等价明确终态）、Repository 端口和 Queue worker acknowledgement；Worker 至少在解析后、写产物前检查取消并原子/一致地终止，API 取消和人工重试要同步 Version 状态。增加 queued、retry-wait、running 三类取消以及取消后人工重试测试。
3. 原件晋升恢复路径不验证已有目标内容：`LocalFileStorage.promote()` 在隔离源消失且目标存在时直接视为恢复成功。必须用会话 `expected_sha256` 校验目标文件；不一致时返回明确错误并禁止创建 DocumentVersion/Job。增加“中断后目标哈希一致可恢复”和“目标被替换/损坏必须拒绝”测试。

修复范围仅限以上一致性和恢复问题；不得进入真实 Zilliz、音频、问答或 G2。全部修改继续保留未提交。

