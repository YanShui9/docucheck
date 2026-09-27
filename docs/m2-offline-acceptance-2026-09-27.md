# M2 离线实现与待验收项（2026-09-27）

本记录核对 `docs/m2-task-breakdown-2026-09-25.md` 的 M2-0～M2-9。2026-09-27 用户明确要求一次完成 M2 的代码工作，替代了此前每次只做一个 M2 小步骤的节奏。以下“通过”只指本机合成资料与脚本化 Provider；没有自动调用 DeepSeek、读取 `.env` 或启动 Docker。

| 步骤 | 当前结果 | 验证与边界 |
| --- | --- | --- |
| M2-0、M2-1 | 此前已完成 | 相邻 XLSX 内存关系和答复证据范围的历史小结仍有效；本次回归覆盖旧用例。 |
| M2-2 | 支持范围内的离线实现完成 | PDF 可提取文字、图片、绘制、注释、疑似无框列布局；XLSX 合并、错误范围、绘图、批注、隐藏行列和工作表、页眉页脚及未知关系引用均有保守覆盖标记。1～7 文件、单文件 10 MiB、PDF 合计 30 页和原有 XLSX 限额在入口检查。复杂无框表格及未列出的 OOXML 扩展仍可能无法可靠恢复语义，遇到不支持结构应标为 `partial`，不能泛称所有 PDF/XLSX 都已完整解析。 |
| M2-3 | 离线完成 | 八个类型化字段，`Decimal` 标准化值、限定语、比较符、型号、按快照/供应商/字段区分的事实集和核验结果；处理状态与五类业务状态分离。 |
| M2-4 | 离线完成 | 全量扫描适用资料，分批抽取并校验来源；检索 top-12 不决定完整性。空候选、同一来源遗漏数值、错误来源或批次失败不发布正式状态。 |
| M2-5.0～5.7 | 离线完成 | 内存 GB/GiB/MiB 与严格比较；其余七字段按各自单位、计价/计数口径、服务类型和交期起点核验；不明口径转人工确认或保持未完成。 |
| M2-6 | 离线完成 | 单家八格和三家 24 格均经规则、工具、LangGraph、答复渲染跑通；不同供应商证据不会混入同一结果。三家合成包为数值相同的隔离样例，不是多报价质量验收。 |
| M2-7 | 离线完成 | `standard`/`thorough` 有独立预算；已验证结果 ID 在工具上下文压缩后保留；短追问继承供应商并由服务端约束工具范围；澄清问题进入下一轮上下文。真实模型的自然语言指代质量仍待探针。 |
| M2-8 | 开发集初测 | 词法召回必要来源 17/17，供应商检索隔离 9/9；脚本化 Agent 任务 2/2、状态 32/32、引用错误 0/76，调用数分别为 9、23。开发集小且脚本预设行动和候选，不能外推真实模型成功率或正式准确率。真实 token、费用和延迟待 M2-9；M5 另需独立封存集。 |
| M2-9 | 离线通过；真实调用待用户 | 后端 217 项测试通过，`ruff check backend evals` 通过，`git diff --check` 无错误。已准备一供应商和三供应商 CLI 合成包，DeepSeek 运行与结果核对需用户本机执行。 |

## 本机离线复现

在已激活的 `docucheck` Conda 环境与仓库根目录执行：

```powershell
python.exe -m pytest backend/tests -q
python.exe -m ruff check backend evals
python.exe -m evals.m2_development --output data/evals/m2-lexical-001.json
python.exe -m evals.m2_agent --output data/evals/m2-agent-scripted.json
```

`evals.m2_agent` 使用代码中的脚本化 Provider；`scripted_usage` 是测试桩填入的单位数，不能当成真实 token 或费用。

## 用户执行的真实模型验收

先运行单家，核对 8 个结果及来源；成功后再运行三家，核对 24 个结果及供应商隔离：

```powershell
python.exe -m backend.app.agent_demo --case-dir examples/dev-pack-001 --provider configured --mode thorough --question "请完整审查供应商 A 的八项指标，并列出每项结果与来源" --output data/m2-agent-demo
python.exe -m backend.app.agent_demo --case-dir examples/dev-pack-003 --provider configured --mode thorough --question "请完整审查全部供应商的八项指标，并列出每家每项结果与来源" --output data/m2-agent-demo
```

两条命令会向已配置的 DeepSeek 服务发送问题、工具定义以及 Agent 读取的**合成资料文字片段**，可能产生多次计费请求；不发送 `.env`、API key 或原始 PDF/XLSX 二进制文件。`thorough` 仍受 12 次决策、24 次工具、32 次模型调用、累计输入/输出预算和 120 秒期限约束。返回 `partial`、`failed` 或 `waiting_user` 时保留 JSON 记录，先按失败码定位原因，不连续盲重试。请只反馈终端状态、八项/24 项结果、调用数及脱敏错误码，不发送密钥或完整请求日志。真实模型结果未回传前，M2 只能记为“离线实现完成，真实模型验收待做”。

正式上传、持久化会话、API/worker 接线属于 M3；前端属于 M4，开始前按用户要求先讨论设计规范与样式。
