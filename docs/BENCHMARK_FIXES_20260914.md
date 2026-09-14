# 六项后端修复记录（2026-09-14）

本轮处理缓存故障、总结资料范围、省略问法、Excel 结构与数值、目录删除、差评验收闭环。
已有工作区修改保留；未执行真实资料全量入库或付费模型调用。

| 问题 | 修复后的行为 | 主要位置 |
| --- | --- | --- |
| 章节已计算，但缓存写入失败导致总结失败 | 缓存读写故障不会丢弃本次章节成果；写入失败时保留有容量和时限的内存副本，重试可以复用。损坏或与原文不符的副本视为未命中。 | `infrastructure/chapter_cache.py`、`infrastructure/overview.py` |
| 只读主文档却显示完整 | 标题命中后继续查找配套资料，合并标题关联与检索找到的文档；分别记录范围是否确认、所选文档是否读完、回答证据是否完整。推断范围不再被标成完整范围。 | `infrastructure/overview.py` |
| “A款支持哪些接口，B款呢？”漏掉 B 款 | 继承前一句的询问内容，为 B 款建立独立检查项；支持连续追问、多个对象和属性组合，保留完整原问题中的条件。 | `domain/question_requirements.py`、`domain/question_coverage.py` |
| 合并标题与 Excel 公式未充分理解 | 区分标题、单层/多层表头和数据行，恢复合并表头的列关系，保留单位与单元格来源；常见公式在本地计算，原公式与结果一并保留。 | `document_processing/spreadsheet_structure.py`、`spreadsheet_formulas.py`、`office_parsers.py` |
| 源文件删除后没有后续处理 | 同步时将无剩余引用的文档写入持久化待撤回列表，与目录快照一起提交；管理者确认后走现有撤回流程。后续同步不会覆盖未处理待办。 | `application/directory_removals.py`、`infrastructure/directory_removal_service.py` |
| 差评没有处理与复测关联 | 新提交的 1—3 分反馈自动建待办，与原反馈同事务保存；支持领取、生成候选验收案例、关联已有案例、准备复测、记录复测、关闭、重新打开和说明后不处理。 | `infrastructure/feedback_capture.py`、`feedback_workflow.py` |

## 缓存与总结范围

- 章节副本缓存的 SQLite 等待时间缩短为 0.2 秒；故障后的内存副本最多 8 MiB，最多保留 10 分钟，且不超过配置的缓存 TTL。
- 原始资料读取、授权与当前版本校验保持有效；仅辅助缓存按可失败处理。
- 范围检查失败但仍有标题命中时，可保留已找到的资料，报告范围尚未确认；权限等必须关闭检索的错误仍按原流程处理。
- 返回报告中的 `scope_confirmed` / `scope_complete` 表示用户明确范围或已确定整个单文档范围；`selected_documents_read_complete` 表示选中资料的遍历是否完成；`complete` 还要求没有读取、章节或最终证据预算缺口。
- 明确指定文档、明确要求总结整个知识库，仍可得到完整报告。无法证明已经找齐相关资料时，会说明范围未确认。
- 问题清单与完整答案缓存的版本已更新，旧缓存不会绕过新的拆分和范围判断。

## Excel 的实际能力与升级

本地计算支持加减乘除、括号、百分数、单元格及范围、工作簿内部跨表引用，以及 `SUM`、`COUNT`、`MIN`、`MAX`、`AVERAGE`、`ABS`、`ROUND`。
例如 `=B3*C3` 会同时保存公式、计算得到的 `200` 和来源 `D3`。

不支持的函数、外部工作簿引用、循环引用、除零、错误单元格或超过计算范围的公式，会明确写入“未计算，不能作为已知数值使用”，不伪造结果。此实现不是完整的 Excel 计算引擎。

表格解析器版本已提升。**已经入库的 Excel 需要针对这些表格重新解析、分块并更新索引，才能使用新的表头和数值。** 不需要为了本轮问答修复重建整个知识库；未变化且向量身份一致的内容继续使用现有向量缓存。目录同步会按各文件所用解析器版本识别需更新的资料。

## 目录删除接口与处理规则

沿用 `scripts/sync_directory.py`。预览只报告；使用 `--apply` 才持久保存删除待办。
API 与同步脚本使用同一个 `storage/sync/directory.sqlite3`，需位于同一份持久化存储。

- `GET /api/spaces/{space_id}/directory-removals`：列出待办及处理历史。
- `POST /api/spaces/{space_id}/directory-removals/{id}/review`：提交 `revision`、`action`、`note`。
- `action` 为 `withdraw`、`keep` 或 `reopen`。

撤回前重新检查源目录可访问、内容与快照一致、文件没有恢复、其他目录没有引用该文档、文档版本没有变化。
检查失败的待办保留为 `failed`，处理者修正原因后按新 revision 重试。正常撤回会停止检索可见性并撤销引用访问，不物理删除文件。
只删除一个重复副本或重命名不会误撤回；文件重新同步恢复后，尚未处理的待办会被取消。

## 差评处理接口

原反馈接口不变；新增返回 `feedback_id`、`work_item_id`。同一次问答的相同评分、原因和说明重复提交时，只保存一次。
新的问答记录包含明确知识库 ID，空库拒答也能正确归属。

- `GET /api/spaces/{space_id}/feedback-work-items?offset=0&limit=100`：分页列表。
- `GET /api/spaces/{space_id}/feedback-work-items/{id}`：详情、原始反馈、案例和处理历史。
- `POST /api/spaces/{space_id}/feedback-work-items/{id}/actions`：提交 `revision`、`action`、`note`，需要时附 `case_id` 或 `attempt_id`。

处理顺序：

1. `claim`：由当前管理者领取，进入 `in_progress`。
2. `create_case`：根据原问题创建候选验收案例并原子关联；或 `link_case` 关联同知识库、同问题的已有案例。
3. 通过现有验收接口，根据原始资料补充正确要求并确认案例。差评说明和现有回答不会自动成为标准答案。
4. `ready_for_retest`：记录案例版本及本轮修复说明，从此时起要求新的复测。
5. 使用现有验收流程执行该案例并进行人工复核；`record_retest` 关联真实 `attempt_id`。
6. 最新复测人工通过后进入 `resolved`；复测失败则回到 `in_progress`。可 `reopen` 重新处理，旧复测不能用于关闭新一轮问题。

案例修改、问题被改成另一道题、旧复测、较新的复测尚未处理、验收环境已变化、没有人工复核或跨知识库案例，均不能直接关闭差评。
`dismiss` 必须填写说明，保存为独立状态，不等于修复通过。

SQLite 与 MySQL 的反馈/待办提交使用同一个事务；新表通过既有工作区建表/迁移机制添加。历史反馈保留原记录，未在本轮批量转换成验收标准或批量执行付费复测。

## 验证

回归覆盖章节缓存故障与复用、缓存容量/TTL/伪造内容、配套资料、省略追问与多对象属性、合并标题与多级表头、公式计算与失败标记、删除持久化/恢复/副本/不可用目录、差评去重/事务回滚/权限/案例和复测版本。

MySQL 保存语句另外通过事务 SQL 测试适配器检查重复提交和回滚；这不等于连接了生产 MySQL 的集成测试。
最终检查结果：

- 全量本地回归：**1933 passed，7 skipped，9 deselected**，耗时 492.93 秒。7 项需要单独启用本地服务/Redis 环境；9 项集成测试按项目默认配置未启用。
- 本轮新增 **32 项**回归检查；与跨文档兼容用例一起执行时 **41 项通过**。
- Mypy：298 个源码文件通过。
- Ruff：后端源码及测试全部通过。
- OpenAPI：普通登录模式和密码登录模式的快照检查均通过。
- `git diff --check` 通过。

最终全量日志：`artifacts/knowledge-benchmark-20260914/full-fixes-final-pytest.log`。
定向复测日志：`artifacts/knowledge-benchmark-20260914/final-focused-fixes.log`。
第一次全量检查中的两处失败，是跨文档测试仍断言“推断范围必须完整”；已保留原回答、来源和引用断言，并补充新的范围语义断言，最终全量通过。

重启使用本仓库的后端和 Worker 后加载新代码。资料转换、实际模型问答及生产数据库集成准确度不由这些本地回归分数代表。
