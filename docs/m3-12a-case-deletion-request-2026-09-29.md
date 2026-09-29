# M3-12a：Case 删除请求与运行栅栏

日期：2026-09-29。本步建立受控清理的入口和事务边界；数据库内容与上传原件留待下一小步清理。

## 实现范围

- `DELETE /api/v1/cases/{case_id}?expected_revision=N` 在 Case 行锁下校验版本并记录 `deletion_requested_at`，返回 `202`、`deleting` 和请求时间。相同 Case 重复请求返回原记录；版本不符返回 `409`。
- 同一事务把该 Case 活跃 Run 及 attempt 设为 `cancelled`，递增 Run fence，写入终止事件；运行中 attempt 的 `outcome_unknown=true`，表示已发出的模型请求可能无法撤回。待派发/发送中的 Outbox 事件、待解析/解析中的文档领取同时撤销。
- 新上传、会话、消息、修正、规则复核、Run 领取/完成/重试/恢复、解析领取及解析结果提交均检查删除标记；API 返回 `case_deleting / 409`。迟到的已派发任务仍可到达 worker，但不能把结果提交为当前结果。其他 Case 保持可用。
- 删除期间保留 Case、快照、证据、历史答复和上传原件供后续受控清理；读取接口仍可查询记录。新迁移为 `0008_case_deletion_request`。

## 验证

- 后端离线测试：`406 passed, 12 skipped`，包含删除 API、重复请求、版本冲突、写入口、活跃 Run/解析栅栏及旧 Outbox 跳过语义。
- `ruff check backend evals`、`pip check`、`git diff --check` 通过。
- 本机 MySQL 8.4 原有容器已健康运行；Alembic 从 `0007_correction_reviews` 升至 `0008_case_deletion_request (head)`。无模型 MySQL 并发测试 `1 passed`：两个删除请求与一次 Run 领取竞争，最终只有一个删除事件，Run、Outbox 和文档均被隔离。该测试会清理自己创建的合成数据。
- 未调用 DeepSeek，未运行 Celery 端到端或真实文件清理。现有长驻 API/worker 若未重启，仍使用旧进程代码；本步验收针对当前源码和数据库迁移。

## 后续边界

`deleting` 是待清理状态，不表示物理删除已经完成。下一小步需设计可重试的数据库与文件清理：按外键顺序处理历史记录，按 `storage_key` 引用计数删除共享原件，并在 worker 迟到结果安全栅栏下完成；不在本步自动实施。
