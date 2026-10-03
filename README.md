# 题目3：LLM Serving 推理服务漂移诊断与灰度放量门禁

本目录是一道面向“推理实现与 LLM serving 栈；服务基础设施与生产监控/漂移”的完整离线工程题。它把模型版本发布这个真实决策拆成 16 份异构原始附件、1560 条请求、72 条评测明细、6 个独立情景、逐 replica/租户/请求交付、as-of 重建、容量估算、延迟/错误 SLO、质量漂移、审批状态、路由结论和安全观测输出。

公开仓库只包含待修复的运行时代码和打包元数据。任务输入、测试、标准实现和评测脚本由 Harbor 任务包单独提供，不会放入本仓库。

## 快速运行

```powershell
cd 题目3\llm-serving-drift-gate
python scripts/seed_demo.py --out fixtures
$env:PYTHONPATH = (Resolve-Path "src")
python -m serving_gate --input fixtures --output output
python -m unittest discover -s tests -v
```

请按照 Harbor 任务中的 `TASK.md` 和合同文档实现完整的离线 serving release gate。实现必须从原始附件独立推导结果，并保持确定性、可审计和安全观测边界。
