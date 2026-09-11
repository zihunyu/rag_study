# 分词器配置与旧索引

本项目固定 `Qwen/Qwen3-Embedding-0.6B` 的 tokenizer，revision 为
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`。原始文件及来源记录位于
`backend/resources/tokenizers/qwen3-embedding-0.6b/`。文件不包含模型权重，运行时不下载文件。

选择依据：[阿里云说明 text-embedding-v4 属于 Qwen3-Embedding 系列](https://www.alibabacloud.com/help/en/model-studio/text-embedding-v4)，
词表来自 [Qwen 官方固定版本](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/blob/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/tokenizer.json)。
这是本地分块预算使用的词表；尚未完成它与托管 `text-embedding-v4` token usage 的全面校准，
不能用它替代供应商账单，也不能假定它等于回答模型的分词器。
9 月 11 日缓存验证的两条短文本，本地共计 12 token，提供方 usage 为 14；这只是小样本，不能推导固定换算比例。

## 已修正的行为

- 生产工厂和 G4 预检共用完整校验：哈希、可加载格式、词表规模、多语言编码覆盖及未知词。
  对测试 WordLevel 词表，改文件名或 ID 不会绕过拒绝。
- artifact 的 truncation/padding 不参与文档切分，正文尾部不会因保存的推理设置被截断。
- BPE 一个字符可能对应多个 token，按原始字符位置切片后重新计数，避免块超预算。
- 短尾块与前块合并时使用原始连续切片，避免重复插入 overlap；父块长度计算包含换行。
- `chunking_revision` 已更新为 `token-aware:v6-bpe-budgets`；artifact 的运行标识含
  `untruncated-v2`，与旧验收身份区分。

## 生效范围

`config/.env` 只需替换 `TOKENIZER_ARTIFACT_PATH`、`TOKENIZER_ARTIFACT_SHA256`、
`TOKENIZER_ID` 三项，值见 `.env.example`。在重启后端和 Worker 前，既有进程不会重新加载配置。
新入库版本使用新词表；已有分块和向量不会自动改写。

旧词表的 `local-functional-wordlevel-v1`、`test-wordlevel` 或其他测试词表标识需要重新处理。
先按文档版本汇总 chunks 中的 tokenizer_id/locator，再读取对应的原始或已审核解析结果。
重建应生成新的文档版本、分块和向量，核对正文、表格、父块、引用及权限后再发布。
不要直接更改旧块的 token_count 或 tokenizer_id：这不能修复边界，也会伪造索引来源。

本次分词器修复不自动批量发布或删除旧知识版本。旧资料全量重建可能调用解析、视觉和
Embedding 服务，应先按受影响文档生成清单，再执行正常入库与复核发布流程。

## 离线验证

```text
.venv/Scripts/python.exe -m pytest -q backend/tests/test_tokenizer_safety.py backend/tests/test_chunking.py backend/tests/test_parent_evidence.py
.venv/Scripts/python.exe scripts/check_env.py --gate G4
```

回归测试覆盖原始 2,200 字中文问题、改名后的测试词表、损坏 JSON、保存的截断参数、英文编号、
表格、罕见汉字、emoji、BPE 切片预算及父块分隔符；不调用外部模型。
