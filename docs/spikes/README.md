# 技术 Harness 运行说明

所有入口直接使用 Python，不要求外部服务即可验证 Harness 自身：

```powershell
& '.\.venv\Scripts\python.exe' scripts/run_spikes.py --all --output-dir artifacts/spikes
```

所有 Harness 使用类型化 `config/.env`，不读取其他用户配置入口。

| Spike | 本地验证 | 真实退出条件 |
| --- | --- | --- |
| MinerU/格式 | G0 六类 60 槽位采集计划、CanonicalDocument Schema、非空节点、locator、自建优先与托管区域门；G1/G2 只跑契约、适配器与合成 Fixture | 六类各 10 份真实难例统一在 G4；验证主/降级路径、资源、定位、ASR 时间戳与失败分类。真实门前不得声明支持 |
| Zilliz Cloud | 中文词项最小 BM25、ACL 交集、过期 watermark fail-closed | 中国区真实集群的 Analyzer、BM25、ARRAY ACL、p95、水位 |
| 模型 | Fake Embedding/LLM/Reranker 的确定性和错误语义 | 候选模型、revision、出站批准、质量、时延、并发、成本 |
| 容量/成本 | 公开公式和 Stub 标记的估算 | 真实页数/文件分布、向量维度、本地磁盘/备份吞吐、预算 |
| 安全合规 | 路径穿越、原子写、进程/依赖扫描、密钥仅状态 | 威胁模型、恶意文件、本地进程隔离、保留/删除/备份签字 |

生成的 JSON 报告写入被忽略的 `artifacts/spikes`；每份报告固定包含
`real_acceptance: false`，不会把 Stub 冒充真实验收。
