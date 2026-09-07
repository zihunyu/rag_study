# 可复现图片字段评测集

`dataset.json` 与 PNG 同目录。6 张由明确表格/连线规格绘制的合成图片；gold 按绘制内容人工定义，未用待测模型生成答案。

- development：420 W、8 W 两张参数表，用于开发。
- heldout：323 W、23 mW 参数表，以及 Gateway → Service 实线和 Gateway ↔ Service 虚线，两张关系图。保留数据行、单位、方向和线型。

真实调用：

```powershell
.venv/Scripts/python.exe scripts/evaluate_visuals.py backend/tests/fixtures/visual_evaluation/dataset.json --run --split heldout --output artifacts/visuals/evaluation.json
```

离线复查：把已保存的 `predictions` 字典保存为独立 JSON，使用 `--predictions path.json` 替换 `--run`。默认不运行真实模型，只有显式 `--run` 会发起调用。CLI 评测独立视觉模型提取/复核；本地 PP-OCRv4 和入库定位由端到端测试单独检查。

字段级统计包含正确/应有/预测单元格、节点和连线数；连线比较包含方向、线型与标签。请持续添加业务人工标注案例，不要把小规模合成集的通过率当作业务总体准确率。
