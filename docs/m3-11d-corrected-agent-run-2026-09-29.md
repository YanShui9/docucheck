# M3-11d：修正快照的 Agent Run

日期：2026-09-29。本步只接通后端已复核事实集与 Agent；不调用真实模型，不处理前端或删除清理。

## 实现范围

- 复核记录存在且能重新通过来源、审计与规则核验时，允许在修正后的当前快照提交 Run。提交前和执行前分别核验；未复核、来源变化或旧版本不会产生新的正式结论。
- 执行器把 M3-11c 保存的整组事实和规则结果绑定到 Agent 工具。针对被修正的供应商与字段，后续 `extract_facts`、`evaluate_checks` 和 `get_check_results` 沿用该组事实；同源旧值和新值不会被再次抽取覆盖。其他字段仍按原链路抽取。旧版内存事实集也能参加多字段审查。
- `GET /api/v1/agent-runs/{run_id}` 增加 `correction_id` 与 `correction_review_status`。正式核验答复对 `needs_review` 附加确定性提示；规则处理状态与人工复核状态保持独立。部分扫描仍无业务状态，即使规则结果为“满足”也保留人工复核提示。
- 修正来源或复核结果在提交后、执行前发生变化时，Run 在调用模型前以 `partial` 和明确错误码结束。没有数据库迁移。

## 本机验证

- `python.exe -m pytest backend/tests -q`：`404 passed, 11 skipped`。新增用例覆盖复核后提交与 API 状态、同源双值沿用、无重复抽取、来源和复核结果篡改、旧内存事实集混合审查，以及“满足”时的人工复核提示。
- `python.exe -m ruff check backend evals`：通过。
- `python.exe -m pip check`、`git diff --check`：通过。
- 本步没有启动 Docker、执行真实 MySQL/Redis/Celery 闭环或调用 DeepSeek。M3-11c 的真实 MySQL 并发记录不能代替本步的端到端验证。

## 边界

- `needs_review` 表示同一原文支持旧值和修正值，不能把修正值视为已确认的唯一事实。用户仍须复核原件含义；规则的业务状态不等于人工确认状态。
- 大快照重复核验的耗时、真实 worker 中的修正 Run、真实采购资料以及前端呈现尚未验收。前端开始前须先与用户确认流程、规范及视觉样式。
