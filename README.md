# 题目3：LLM Serving 推理服务漂移诊断与灰度放量门禁

本目录是一道面向“推理实现与 LLM serving 栈；服务基础设施与生产监控/漂移”的完整离线工程题。它把模型版本发布这个真实决策拆成 16 份异构原始附件、1560 条请求、72 条评测明细、6 个独立情景、逐 replica/租户/请求交付、as-of 重建、容量估算、延迟/错误 SLO、质量漂移、审批状态、路由结论和安全观测输出。

标准实现位于 `src/serving_gate/`，合成原始附件位于 `fixtures/`，公开回归测试位于 `tests/`。不需要安装第三方库，也不需要启动真实模型或访问网络。

## 快速运行

```powershell
cd 题目3\llm-serving-drift-gate
python scripts/seed_demo.py --out fixtures
$env:PYTHONPATH = (Resolve-Path "src")
python -m serving_gate --input fixtures --output output
python -m unittest discover -s tests -v
```

标准数据下 `base=CANARY`、`gpu-pressure=HOLD`、`quality-drift=ROLLBACK`、`revoked-approval=ROLLBACK`；`pre-release` 因评测证据不完整而 `HOLD`，`tenant-skew` 受到质量/事故证据约束而 `ROLLBACK`。这些结论由输入附件和 policy 计算得出，测试会检查时点截断、权威遥测、撤销即时性、逐条对账、固定指标标签、敏感信息边界和确定性输出。
