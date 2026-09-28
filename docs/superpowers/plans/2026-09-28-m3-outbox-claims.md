# M3-8a Outbox 领取与过期恢复实施计划

> 本计划只实施数据库 Outbox 的领取状态机；不发布 Redis 消息、不启动 Celery worker、不调用模型。下一小步再连接发布器和 worker。

**目标：** 多个派发进程可安全领取同一数据库中的待发 Run 事件，失败或进程退出后可重领；过期领取者不能确认新一轮的事件。

**架构：** 复用已有 `outbox` 表。`pending` 行到期后使用短事务与 `SELECT ... FOR UPDATE SKIP LOCKED` 领取，改为 `sending`；`available_at` 在 `sending` 时表示租约到期时刻。每次领取递增 `delivery_attempts`，其数值作为确认/退回的 fence。确认改为 `delivered`，失败退回 `pending` 并设置下次可领取时刻。发布网络请求在后续小步、数据库事务之外进行。

**技术栈：** SQLAlchemy Core、MySQL 8.4 InnoDB、SQLite 离线测试、pytest。MySQL 官方明确 `SKIP LOCKED` 适用于队列表；[锁定读文档](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)。

## Task 1：失败测试

**文件：** 新建 `backend/tests/test_outbox_repository.py`，复用 `backend/app/db/models.py` 中已有的 `metadata`、`outbox`、`agent_runs`、`run_attempts`。

- [x] 构造 SQLite 临时库及最小 Case、快照、会话；用 `RunRepository.submit(SubmitRun(...))` 生成真实的 `pending` Outbox 行。
- [x] 断言 `OutboxRepository.claim(now=t0, lease_seconds=30)` 返回同一个 `run_id`、`attempt_id` 和 `event_key`，状态变为 `sending`、次数变为 1；租约未到期时再次领取返回空。
- [x] 到 `t0+31s` 重领，次数变为 2；旧次数 1 的 `mark_delivered`/`release` 均返回 `False`，次数 2 确认成功且终态不可重领。
- [x] 另用例将一次发送失败退回 `pending`、设置 10 秒延迟；到期前不可领，到期后可领。非法批量大小、租约和延迟被拒绝。
- [x] 在实现之前运行 `python.exe -m pytest backend/tests/test_outbox_repository.py -q`，确认因缺 `OutboxRepository` 而失败。

## Task 2：最小实现

**文件：** 新建 `backend/app/db/outbox_repository.py`。

- [x] 定义不可变 `OutboxClaim`：`id`、`event_key`、`run_id`、`attempt_id`、`event_type`、`payload`、`delivery_attempts`；公共 API 为 `claim(limit=10, lease_seconds=60, event_type='run.dispatch', now=None)`、`mark_delivered(id, delivery_attempts, now=None)`、`release(id, delivery_attempts, retry_delay_seconds, now=None)`。
- [x] `claim` 在 `engine.begin()` 内选择 `pending` 或租约到期的 `sending`，只取 `available_at <= now`，按时间和 ID 排序；使用 `with_for_update(skip_locked=True)` 并在更新时再检查旧状态、旧次数和到期时间。
- [x] `mark_delivered` 与 `release` 使用 `status='sending' AND delivery_attempts=:fence` 条件更新，返回 `rowcount == 1`；成功确认记录 `delivered_at`，失败退回设置新的 `available_at`。
- [x] 时间一律为 UTC 的无时区 `datetime`，与现有 MySQL `DATETIME` 契约一致。参数不合规抛 `ValueError`；不存储发布异常原文或敏感信息。
- [x] 运行定向测试、`python.exe -m pytest backend/tests -q`、`python.exe -m ruff check backend evals` 与 `git diff --check`。

## Task 3：记录与交接

**文件：** 更新 `CLAUDE.md`、`README.md`、`docs/development-roadmap.md`；新建 `docs/m3-8a-outbox-claims-2026-09-28.md`。

- [x] 记录实际测试结果，明确本步尚未发布 Redis、未运行 Docker/worker、未调用模型。
- [x] 下一小步接发布器及 worker；对发布成功但确认落库失败的重复消息继续依靠 `RunRepository.claim` 只领取一次，并验证 worker 崩溃与恢复。
