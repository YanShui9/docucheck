# M3-11b：有来源依据的事实修正与新版本

日期：2026-09-29。用户选择“只修正提取事实”；本步没有改动原 PDF/XLSX，也没有让模型直接改采购结论。

## 实现

- `GET /api/v1/agent-runs/{run_id}/facts` 复核 Run 的 v2 事实集后提供可选事实及当前版本标志。旧 v1、未完成或超限记忆返回 `available=false`；篡改事实集返回 `memory_scope`。
- `POST /api/v1/cases/{case_id}/corrections` 接受来源 Run/事实集/事实 ID、新原始数值与单位、理由、预期版本和 `Idempotency-Key`。证据 ID、字段、供应商与比较口径从服务端已验证事实继承，客户端不能覆盖；新值须通过同一原文证据验证，且归一化后的值确实变化。
- 同一 MySQL 事务锁定 Case，克隆已解析文档与证据到新快照，写入 `corrections` 追加审计及幂等记录，并递增 Case 版本。原文、哈希和旧版本不变，新证据 ID 映射可通过 `GET /api/v1/cases/{case_id}/corrections/{correction_id}` 查询。旧 Run 的 `is_current` 立即为 `false`。
- 修正回执为 `pending_recheck`。新快照仍是原始资料，M3-11c 尚未对修正事实执行规则重算；API 和 Run repository 暂时拒绝在这一版本提交新 Run，返回 `correction_pending_recheck`。服务端没有自动创建 Run，也没有调用模型。

## 验收

- 离线 `python.exe -m pytest backend/tests -q`：`387 passed, 11 skipped`。包含来源验证、篡改拒绝、跨 Case、活跃 Run、幂等、审计读取、版本失效及迁移升降与元数据比较。
- 真实 MySQL 已升至 `0006_corrections (head)`。独立的合成来源并发测试 `1 passed`：同版本不同幂等键两个提交只有一个成功；随后同键重放返回原回执。测试不会生成 Outbox，也不调用 DeepSeek。
- `ruff check backend evals`、`pip check`、`git diff --check` 通过。操作前停止了自动派发；确认数据库没有待执行 Run 或待发送 Outbox 后，重新构建并恢复 worker/dispatcher，worker 显示 `healthy`。

## 边界与后续

- 本步仅形成受原文支持的修正记录及新版本，尚无正式新业务状态。后续 M3-11c 已在新快照重新验证映射证据，保留同源旧值与新值，确定性重算并保存规则结果；由于 Agent 尚未消费修正事实，Run 仍保持阻断。不能把修正回执展示为“满足/不满足”的新结论。
- 历史 v1、缺失或超限事实集不能直接修正；须重新完成可保存事实集的 Run。复制上限为 25,000 证据块/50 MiB Agent 可读文本；大资料的事务耗时和存储增量尚未测量。删除清理、权限控制、前端与真实采购资料集验收仍待后续阶段。
- 参考 [MySQL InnoDB 的行锁与隔离级别](https://dev.mysql.com/doc/refman/8.4/en/innodb-transaction-isolation-levels.html)、[SQLAlchemy 的显式事务边界](https://docs.sqlalchemy.org/en/21/changelog/migration_20.html) 和 [Alembic 操作参考](https://alembic.sqlalchemy.org/en/latest/ops.html)。当前选择 Case 行锁加唯一约束；不同业务写路径的死锁重试策略与大快照性能需后续测量和收敛。
