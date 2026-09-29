# M3-11c：人工修正后的确定性规则复核

日期：2026-09-29。用户确认同一原文同时支持旧值与修正值时，保留两者并标记人工复核。本步不改原 PDF/XLSX、不调用 DeepSeek。

## 实现

- `POST /api/v1/cases/{case_id}/corrections/{correction_id}/recheck` 读取 M3-11b 的修正审计与来源 Run 的 v2 事实集，逐项映射到新快照证据并重新核验原文、角色、供应商、文档哈希、解析版本和位置。新旧证据或审计映射不一致时拒绝写入。
- 被修正的旧值与新值都保留在新事实集中；现有 `evaluate_field` / `evaluate_memory` 重新计算受影响字段。复核结果追加到 `correction_reviews`，同一修正只允许一条，重复 POST 返回相同结果。历史版本的已完成复核仍可读取。
- `GET` 修正审计现在返回 `corrected_fact`、`check_result` 与 `review_status`。由于两值由同一来源支持，`review_status=needs_review`；规则处理状态独立记录。扫描不完整时 `processing_status=partial`、`business_status=null`。资料完整时业务状态仍由规则决定，可能为“待人工确认”。
- 复核计算发生在写事务外，写入时锁定 Case 并再次核对版本；并发相同请求最多保存一条记录。已保存复核绑定完整证据文本及覆盖指纹，读取时若来源被篡改会拒绝。

## 验证

- 离线后端：`396 passed, 11 skipped`；包括字段与历史内存事实集、部分扫描、完整扫描中旧值与新值共存、原文和审计篡改、来源记忆篡改、历史版本、重复请求，以及 Alembic SQLite 升降与元数据检查。
- MySQL 8.4 已迁移至 `0007_correction_reviews (head)`。合成来源并发测试 `1 passed`：两次并发复核返回相同内容，仅保存一条 `correction_reviews`。该测试不产生 Outbox，不调用模型。
- `ruff check backend evals`、`pip check`、`git diff --check` 通过。

## 边界及下一步

- 规则复核并不等于接受人工修正为新的唯一事实。若旧值与新值均有原文依据，仍需人工确认原件含义；新快照不能据此自动得到“满足”。
- 新快照的 Agent Run 仍返回 `correction_pending_recheck`：Agent 抽取工具尚未消费这份复核事实集，直接放行可能让对话与复核不一致。下一步需设计修正事实注入 Agent 与 Run 结果展示的契约，再解除阻断。
- 正式上传资料缺少可靠型号绑定时，现有规则保守给出“待人工确认”。大快照重算的 2 秒质量目标、删除清理、真实采购资料和前端均未验收。
- 并发事务采用短写事务及 Case `FOR UPDATE`；参考 [MySQL InnoDB 锁定读](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html) 与 [SQLAlchemy 事务上下文](https://docs.sqlalchemy.org/en/20/core/connections.html)。
