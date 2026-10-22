# DocuCheck 文档导航

本页是对外技术文档入口。最新整理日期：2026-10-22；各验证记录以其自身的日期、源码和数据范围为准。

## 推荐阅读顺序

| 目的 | 文档 |
| --- | --- |
| 两分钟了解业务与能力 | [项目首页](../README.md) |
| 了解 Agent、RAG、记忆与可靠执行 | [架构与设计取舍](architecture-and-interview.md) |
| 准备十分钟现场演示 | [演示指南](interview-demo.md) |
| 核对测试与业务成绩 | [评估与验证](evaluation.md) |
| 安装和启动 | [复现手册](reproduction.md) |
| 了解公开材料与安全边界 | [安全说明](../SECURITY.md) |
| 查看当前阶段和待验收条件 | [开发路线图](development-roadmap.md) |
| 查看冻结范围、缺陷和面试收尾安排 | [当前收尾计划与基线](superpowers/plans/2026-10-13-project-consolidation.md) |
| 核对最新修复的实际运行与失败/重试 | [B服务应用、核验与检索回读](repair-consolidation-b-services-2026-10-13.md) |

## 按模块深入

| 模块 | 实现与验证入口 |
| --- | --- |
| 动态指标与确认协议 | [动态清单实现](dynamic-checklists-b-2026-10-04.md)、[聊天助手接口](backend-assistant-api-2026-10-04.md) |
| 上下文与分层记忆 | [记忆模块](context-memory-2026-10-04.md)、[分阶段上下文](repair-assistant-context-stages-2026-10-09.md)、[后续上下文复审](repair-assistant-context-audit-2026-10-09.md) |
| 上下文管理升级 | [A2b完整请求缩批与正式重试](context-batching-A2b-2026-10-22.md)、[B1同对话语义摘要](semantic-memory-B1-2026-10-22.md)、[B2跨对话与版本交接](task-handoff-B2-2026-10-22.md)、[本步计划](superpowers/plans/2026-10-22-context-batching-A2b.md)、[A2a容量与累计额度分离](context-capacity-A2a-2026-10-21.md)、[A1请求计量与拒绝诊断](context-request-diagnostics-2026-10-21.md)、[轻量设计稿](superpowers/specs/2026-10-21-context-management-design.md)；A2b源码与完整离线验收通过：2279 passed/56 skipped、网络0，依赖/Ruff及独立复核通过；B1源码与离线验收完成：2341 passed/56 skipped、网络0，依赖/Ruff与两轮独立复核通过；B2源码与离线验收完成：2405 passed/59 skipped、网络0，依赖/Ruff及两轮独立复核通过，临时MySQL8.0.41实连4项通过；C用量界面待讨论，尚未应用运行服务 |
| 混合检索与异步索引 | [检索组件](hybrid-retrieval-2026-10-03.md)、[持久化索引](durable-indexing-2026-10-03.md)、[真实浏览器 RAG](repair-browser-rag-live-2026-10-08.md) |
| 最新资料与品目对应 | [C闭环验收](document-item-binding-c-acceptance-2026-10-11.md)、[A1 契约与 API](document-item-binding-a1-2026-10-10.md)、[A2 规则与运行](document-item-binding-a2-2026-10-10.md)、[A3 范围检索](document-item-binding-a3-2026-10-10.md)、[B 中文前端与验证](document-item-binding-b-2026-10-10.md)、[C 指标与原件补修](document-item-binding-c-metric-labels-2026-10-11.md)、[人工继承补修](document-item-binding-c-manual-carry-2026-10-11.md) |
| 清单问答与资料补修 | [已确认规则短答、关联续用与历史读取](repair-remaining-chat-materials-2026-10-20.md)、[服务更新](service-checklist-update-2026-10-21.md)、[本轮计划](superpowers/plans/2026-10-20-remaining-chat-material-repairs.md) |
| 当前动态多品类质量 | [评估说明](evaluation.md)、[2026-10-14 测量摘要](../evals/reports/dynamic-development-2026-10-14.json)；12 包开发测量已交付，质量出口未通过，独立封存仍为 0；10 月 15 日 v12 源码修复尚未真实模型复测 |
| 评估协议与合成资料 | [设计与评分口径](superpowers/specs/2026-10-11-dynamic-evaluation-design.md)、[12 包蓝图](../evals/dynamic/case_matrix.json)、[核实来源](../evals/dynamic/source_catalog.json)、[D1 准备记录](dynamic-evaluation-d1-2026-10-11.md)；动态合成资料的生成、校验方法见[评估工具说明](../evals/README.md) |
| 历史固定字段开发验证 | [M5-B 原始开发验证](m5-b-development-validation-2026-10-03.md)、[后续同版本联调](m5-current-version-integration-2026-10-03.md)；104/104 不作为当前多品类准确率 |

这些是分阶段技术记录，文中的“当前”“尚未接入”和“未配置远程”等描述仅适用于当时交付。跨阶段理解能力请先读首页和路线图的最新入口。

## 开发协作与历史

- [原 README 开发历史](development-history.md)：归档原入口，保留成功、失败与改进过程。
- [旧项目上下文](archive/2026-10-10-claude.md)、[旧路线图](archive/2026-10-10-roadmap.md)、[旧小步计划](archive/2026-10-10-step-by-step.md)：保留完整阶段记录，当前执行从简洁入口开始。
- [当前协作协议](step-by-step-plan.md)、[流程优化设计](superpowers/specs/2026-10-10-development-workflow-design.md)、[三步实施计划](superpowers/plans/2026-10-10-development-workflow.md)。
- [统一开发验证入口](development-workflow.md)：风险计划、离线定向/全量、前端门禁与真实业务验收协议。
- [交付审查模板](templates/change-review.md)：业务断言、反例、身份/证据、学习复盘与收益记录。
- [流程交付和复审](development-workflow-review-2026-10-10.md)、[机器证据摘要](development-workflow-evidence-2026-10-10.json)：本次真实验证、文档体积变化和未测收益。
- [CLAUDE.md](../CLAUDE.md) 与 [AGENTS.md](../AGENTS.md)：AI 开发协作约定，不是产品用户手册。
- `superpowers/specs/` 与 `superpowers/plans/`：设计讨论、范围约束与分阶段计划。

原始运行日志和部分归档资产位于开发机忽略目录 `data/`。仓库内的报告可供审阅，但外部访客不会仅靠克隆获得这些本地资产；离线测试按[复现手册](reproduction.md)执行，动态合成资料生成按[评估工具说明](../evals/README.md)执行。
