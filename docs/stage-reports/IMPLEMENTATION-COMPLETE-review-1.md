# 全部实现完成包第一次审核

审核结果：`CHANGES_REQUIRED:IMPLEMENTATION_COMPLETE_PRE_REAL_VALIDATION`

审核时间：2026-09-01T14:27:27+08:00

提交策略：`NO_COMMITS`。

## 已通过

- 最终统一验证计划固定 `BLOCKED_REAL_EVIDENCE_MISSING`，synthetic evidence 无法解锁；
- Pilot/UAT/observation/final report 的响应均标记 simulated/real_acceptance=false；
- diagnostics、runtime events、plan-only operations、offline assurance、Vue dashboard 和文档骨架已建立；
- 全量质量门通过：205 tests、Ruff 209 files、mypy 92 source files、OpenAPI 48 paths、SQLite v13、Vue build、密钥扫描和 Docker 禁止项；
- 真实样本、真实模型、外部迁移/清理/流量/观察均未执行。

## 必须修复

### 1. P0：`local_stack stop` 会终止状态文件中的任意 PID

状态文件位于可写的 `data/storage/temp/local-stack.json`。stop 直接遍历其中 PID 并调用
`os.kill(pid, SIGTERM)`，没有验证进程可执行文件、命令行、cwd、启动时间或父进程是否属于本项目。

独立安全探针把状态写成 `{"unrelated": 424242}` 并替换 kill 为记录器；stop 会尝试向该 PID
发送信号并报告 graceful=true。陈旧 PID 被系统复用时可能误杀无关用户进程。

修复标准：状态记录 PID、进程创建时间、可执行文件、规范化命令、cwd 和随机 owner token；status/stop 使用只读进程检查验证全部字段后才允许信号。任何不匹配必须 refuse，不删除证据；start 防重复并验证旧状态；部分启动失败要清理已启动的项目子进程；stop 等待退出并只对仍属于本次 owner 的进程做受控升级。新增伪造/陈旧 PID、PID 复用、部分启动失败、重复 start/stop 测试，绝不在测试中真实杀无关进程。

### 2. P0：7 天 observation 可在创建后立即、无指标地完成

`create_observation()` 只写 `ends_at=starts_at+7天`；`evaluate_observation()` 仅检查计划区间长度，
没有检查当前时间是否已经达到 ends_at、窗口是否关闭、指标是否齐全。独立 API 探针创建窗口后不写任何 metrics，补齐四个 synthetic signoff，立即得到 `SIMULATED_COMPLETE`。

修复标准：GovernanceService 注入 clock；服务端控制/校验窗口开始，只有 `clock>=ends_at` 且窗口显式 CLOSED、必需指标存在并覆盖观察区间时才能 simulated complete。缺指标、未来开始、未到 7 天、采样缺口均 BLOCKED。合成测试用 fake clock 快进，不得通过伪造 starts_at 冒充实际经过时间；真实 acceptance 仍固定 blocked。

### 3. P1：Pilot 未运行 canary/UAT 即可 rollout

当前只要 technical/security/SRE 三方 synthetic APPROVE 且无 P0/P1 defect，便可直接生成
5/25/50/100 rollout；canary 是未持久化的独立接口，UAT 与 Pilot readiness 没有关联。

独立探针未调用 canary、未创建 UAT，rollout 仍返回 200。

修复标准：持久化 canary run/seed/result/threshold；Pilot readiness 必须要求最近 canary pass、关联 UAT suite 全部 PASSED 且有 evidence、无 P0/P1、三方 signoff 无 VETO。rollout 只允许从明确的 SIMULATED_GO 状态转换一次；canary/UAT 失败触发 NO_GO/rollback。

### 4. P1：UAT 可用空 evidence 标记 PASSED

`UATResultRequest.evidence` 默认空列表，独立探针 `result=PASSED,evidence=[]` 返回 200。步骤也没有逐步结果或 expected 对账。

修复标准：PASSED 必须有不可变 evidence reference、每个 step 的结果和 expected 对账；FAILED/BLOCKED 可附缺陷。evidence 只能引用 evidence index 中存在的 synthetic revision；最终真实 UAT 仍要求 real evidence category，synthetic 不可解锁。

### 5. P1：Governance 写操作缺少幂等和并发控制，重复 rollout 返回 500

Pilot/UAT/observation/signoff/defect/incident 等写 API 没有统一 Idempotency-Key/request hash/ETag。
独立探针第二次调用同一 Pilot rollout 触发唯一约束，返回非 JSON 500。

修复标准：创建和动作端点使用 `(tenant, operation, key)` + request hash；同请求稳定 replay，异请求 409；聚合更新使用 ETag/If-Match 或 row version CAS。重复 rollout/rollback/signoff/resolve/metrics/result 不得产生 500 或重复记录，错误必须使用统一 JSON contract。

## 边界

上述全部可使用本地合成数据修复，不需要真实样本或外部授权。不得开始最终统一真实验证，不得宣称 G5/G6 通过。
