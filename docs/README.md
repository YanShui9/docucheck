# DocuCheck 技术文档

从业务流程、核心设计到验证记录，按需选择阅读入口。最新整理：2026-10-24。

## 推荐阅读

| 想了解什么 | 文档 |
| --- | --- |
| 产品解决的问题与使用流程 | [项目首页](../README.md) |
| Agent、RAG、规则与可靠执行如何配合 | [架构与设计取舍](architecture-and-interview.md) |
| 从需求到报告的现场演示 | [演示指南](interview-demo.md)、[公开合成报价](../examples/showcase/README.md) |
| 安装、测试与本机服务配置 | [复现手册](reproduction.md) |
| 测试、业务案例与质量测量 | [评估说明](evaluation.md) |
| 公开材料与配置边界 | [安全说明](../SECURITY.md) |

## 最近验证

| 日期与范围 | 记录 |
| --- | --- |
| 2026-10-24：旧12包＋新6包首轮，完整分母、失败与用量 | [业务复测与问题定位](current-quality-retest-2026-10-24.md)、[机器摘要](../evals/reports/current-quality-retest-2026-10-24.json) |
| 2026-10-24：先结论、编号来源与原始答复；324项、桌面/窄屏/历史核对 | [展示交付](answer-source-readability-2026-10-24.md) |
| 2026-10-24：后端 2588 通过 / 62 跳过、前端 302 项，固定源码本机服务及原报价审查 | [金额税口径部署与实连](money-tax-live-2026-10-24.md) |
| 2026-10-24：数量清单条件更正，2 项正式结果及来源 / 导出 / 历史一致 | [数量条件更正与验收](quantity-condition-live-2026-10-24.md) |
| 2026-10-23：原采购任务最早原话定位与出处回读 | [原话定位与服务验收](first-task-message-2026-10-23.md) |
| 2026-10-17：新任务双品目完整浏览器演练 | [流程、计时与验证范围](evaluation.md#20-2026-10-17-完整连续浏览器演练)、[机器摘要](../evals/reports/continuous-browser-2026-10-17.json) |

运行记录和历史测量分别绑定当时的源码、资料与日期。指定合成案例与源码回归不替代独立质量评估，完整分母及失败记录保留在[评估说明](evaluation.md)。

## 按模块深入

| 模块 | 设计与验证入口 |
| --- | --- |
| 动态清单与确认 | [指标契约](dynamic-checklists-b-2026-10-04.md)、[助手接口](backend-assistant-api-2026-10-04.md)、[清单问答与关联续用](repair-remaining-chat-materials-2026-10-20.md) |
| 资料与品目范围 | [API 与契约](document-item-binding-a1-2026-10-10.md)、[规则与运行](document-item-binding-a2-2026-10-10.md)、[范围检索](document-item-binding-a3-2026-10-10.md)、[中文前端](document-item-binding-b-2026-10-10.md)、[指定闭环](document-item-binding-c-acceptance-2026-10-11.md) |
| 金额与税口径 | [服务端独立来源检查](money-tax-sources-2026-10-24.md)、[v14 元数据边界与验收](money-tax-live-2026-10-24.md) |
| 请求容量与批次 | [完整请求诊断 A1](context-request-diagnostics-2026-10-21.md)、[容量与累计额度 A2a](context-capacity-A2a-2026-10-21.md)、[拆批与正式重试 A2b](context-batching-A2b-2026-10-22.md) |
| 记忆与历史 | [同对话语义摘要 B1](semantic-memory-B1-2026-10-22.md)、[跨对话任务交接 B2](task-handoff-B2-2026-10-22.md)、[用量查看 C](context-usage-C-2026-10-23.md)、[历史回读](history-read-recovery-2026-10-23.md) |
| 检索与索引 | [混合检索](hybrid-retrieval-2026-10-03.md)、[持久化索引](durable-indexing-2026-10-03.md)、[浏览器原文问答](repair-browser-rag-live-2026-10-08.md) |
| 异步执行与恢复 | [架构中的执行时序](architecture-and-interview.md#5-异步执行与失败恢复)、[服务集成验证](repair-consolidation-b-services-2026-10-13.md) |
| 业务评估 | [评分协议与测量](evaluation.md)、[12 包蓝图](../evals/dynamic/case_matrix.json)、[资料来源](../evals/dynamic/source_catalog.json)、[评估工具](../evals/README.md) |

## 工程记录

- [当前路线图](development-roadmap.md)、[协作流程](development-workflow.md)和[交付审查模板](templates/change-review.md)记录执行约定与验收条件。
- [CLAUDE.md](../CLAUDE.md)、[AGENTS.md](../AGENTS.md)用于代码协作；设计和实施计划位于 `superpowers/specs/`、`superpowers/plans/`。
- [早期首页归档](development-history.md)和[2026-10-24 首页归档](archive/2026-10-24-project-overview.md)保留阶段变化；历史中的“当前”和“未部署”按当时日期理解。

公开仓库提供源码、合成示例和可审阅报告。部分原始日志与历史资料位于本机忽略目录 `data/`，不会随 Git 克隆取得；离线测试与合成资料生成方法见[复现手册](reproduction.md)和[评估工具说明](../evals/README.md)。
