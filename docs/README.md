# DocuCheck 文档导航

本页是对外技术文档入口。最新整理日期：2026-10-10；各验证记录以其自身的日期、源码和数据范围为准。

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

## 按模块深入

| 模块 | 实现与验证入口 |
| --- | --- |
| 动态指标与确认协议 | [动态清单实现](dynamic-checklists-b-2026-10-04.md)、[聊天助手接口](backend-assistant-api-2026-10-04.md) |
| 上下文与分层记忆 | [记忆模块](context-memory-2026-10-04.md)、[分阶段上下文](repair-assistant-context-stages-2026-10-09.md)、[后续上下文复审](repair-assistant-context-audit-2026-10-09.md) |
| 混合检索与异步索引 | [检索组件](hybrid-retrieval-2026-10-03.md)、[持久化索引](durable-indexing-2026-10-03.md)、[真实浏览器 RAG](repair-browser-rag-live-2026-10-08.md) |
| 最新资料与品目对应 | [A1 契约与 API](document-item-binding-a1-2026-10-10.md)、[A2 规则与运行](document-item-binding-a2-2026-10-10.md)、[A3 范围检索](document-item-binding-a3-2026-10-10.md) |
| 测试及开发集质量 | [M5-B 原始开发验证](m5-b-development-validation-2026-10-03.md)、[后续同版本联调](m5-current-version-integration-2026-10-03.md) |

这些是分阶段技术记录，文中的“当前”“尚未接入”和“未配置远程”等描述仅适用于当时交付。跨阶段理解能力请先读首页和路线图的最新入口。

## 开发协作与历史

- [原 README 开发历史](development-history.md)：归档原入口，保留成功、失败与改进过程。
- [旧项目上下文](archive/2026-10-10-claude.md)、[旧路线图](archive/2026-10-10-roadmap.md)、[旧小步计划](archive/2026-10-10-step-by-step.md)：保留完整阶段记录，当前执行从简洁入口开始。
- [当前协作协议](step-by-step-plan.md)、[流程优化设计](superpowers/specs/2026-10-10-development-workflow-design.md)、[三步实施计划](superpowers/plans/2026-10-10-development-workflow.md)。
- [CLAUDE.md](../CLAUDE.md) 与 [AGENTS.md](../AGENTS.md)：AI 开发协作约定，不是产品用户手册。
- `superpowers/specs/` 与 `superpowers/plans/`：设计讨论、范围约束与分阶段计划。

原始运行日志和部分归档资产位于开发机忽略目录 `data/`。仓库内的报告可供审阅，但外部访客不会仅靠克隆获得这些本地资产；离线测试和合成资料生成按复现手册执行。
