# UAT parser/layout/fixture remediation 部分完成状态

状态：`PARTIAL_LOCAL_ONLY`，尚未可申请 retest runner 审核。

已完成的通用修复：DOCX parser 从“全部段落后全部表格”的聚合读取改为按 body XML child 顺序读取段落与表格容器，修复相邻容器关联和阅读顺序的通用缺口。`backend/tests/test_parsers.py` 定向通过（5 passed），Ruff 通过。

未完成：PPTX shape/table 容器分组与双栏顺序、Spreadsheet/CSV 行列顺序回归、fixture glyph coverage/font fallback gate、受影响 fixture 重生及 metadata/SHA lineage、fresh retest vN 动态输入和完整质量门。

本轮没有 fixture、metadata、历史 UAT result/checkpoint/Gate、provider、Zilliz、Docker 或 commit 变动；没有任何单条内容、答案、姓名、日期、实体或事实硬编码。
