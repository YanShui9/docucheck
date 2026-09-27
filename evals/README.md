# M2 开发评测

这里是可重复运行的**合成开发集**，与 `examples/dev-pack-001/labels.json` 分开。`cases/lexical-001.json` 的 `documents[].blocks` 构成检索输入，`queries[].relevant` 只在检索结束后计分；标注不会进入证据正文、检索查询扩展或 Agent 提示。此集只用于开发，不能代替 M5 的独立封存验收集。

在已有的 `docucheck` Conda 环境中，从仓库根目录运行：

```powershell
python.exe -m evals.m2_development --output data/evals/m2-lexical-001.json
python.exe -m evals.m2_agent --output data/evals/m2-agent-scripted.json
python.exe -m pytest backend/tests/test_m2_development_eval.py -q
```

第一个命令只读取合成 JSON，在本机调用现有词法检索和规则边界；不会调用模型 API、Docker 或读取 `.env`。不加 `--output` 时将 JSON 报告打印到终端。报告保留每个查询的必要来源分子/分母、漏召回位置、跨供应商引用数。无命中查询的必要来源分母为 0，不计入总体召回率。`guardrails` 是三个离线边界探针的通过数，**不是**真实 Agent 的无效引用率或业务正确率。

2026-09-27 的基线：增加字段别名之前，8 个有标注查询的必要来源召回为 **14/17**，漏掉“显示屏对角线”与“屏幕尺寸”、“保修期”与“质保期限”、“交货”与“交付期限”三组对应来源。加入词法别名后的同一数据为 **17/17**；另有 1 个无命中查询，供应商隔离 **9/9**，无效引用拒绝、部分覆盖不发布正式状态、无命中不当作字段缺失各 **1/1**。这些小样本数字只用于追踪本次检索改动，不能推断真实文件上的召回率。

`m2_agent.py` 读取经哈希核验的单家和三家合成包，使用**脚本化 Provider**驱动真实 LangGraph、抽取、规则和答复链路，不联网。当前开发结果是 Agent 任务 2/2、状态 32/32、来源范围错误 0/76；模型调用分别为 9 与 23。报告的 `scripted_usage` 是测试桩写入的计数值，不能当作 DeepSeek token、费用或真实性能。真实模型任务成功率、调用数、token 和延迟须由下方用户探针另行记录。

## M2-9 真实 DeepSeek 探针（由用户执行）

完成八字段 Agent 和 CLI 回归后，用户在本机已配置的 `docucheck` 环境运行一供应商的合成包：

```powershell
python.exe -m backend.app.agent_demo --case-dir examples/dev-pack-001 --provider configured --mode thorough --question "请完整审查供应商 A 的八项指标，并列出每项结果与来源" --output data/m2-agent-demo
```

单家通过后，三供应商使用已构建并标注的 `examples/dev-pack-003` 合成包：

```powershell
python.exe -m backend.app.agent_demo --case-dir examples/dev-pack-003 --provider configured --mode thorough --question "请完整审查全部供应商的八项指标，并列出每家每项结果与来源" --output data/m2-agent-demo
```

这两条命令会向已配置的 DeepSeek 服务发送问题、工具定义及 Agent 选取的合成资料原文片段，可能产生多次计费请求；不会发送 `.env` 或原始 PDF/XLSX 二进制文件。运行前确认模型账户可用。请反馈终端状态、八项/24 项结果、模型调用数和脱敏后的失败码；不发送 API key、`.env` 或完整请求日志。若返回 `partial`、`failed` 或 `waiting_user`，保留记录并先定位原因，不连续盲重试。三家资料数值刻意相同，只检验 24 格及跨家引用；没有用户运行回执时，不得把 M2-9 标为真实验收通过。
