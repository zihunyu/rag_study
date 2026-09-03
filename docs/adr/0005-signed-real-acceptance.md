# ADR 0005：真实验收必须使用受保护密钥签名

状态：Accepted

普通 SHA-256 只能证明内容未变，不能证明证据由可信流程产生。真实验收文件必须绑定 Provider、
模型/Prompt/索引/数据集版本、指标、阈值、Git Commit、CI Run、质量报告哈希和时间戳，并由
受保护 Environment 中的 `RAG_ACCEPTANCE_SIGNING_KEY` 生成 HMAC。Runtime 同时验证签名、有效期
和所有阈值；配置开关、未签名文件、过期文件或低于阈值的结果均不能产生
`real_acceptance=true`。
