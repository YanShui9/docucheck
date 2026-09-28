# M3-8a Outbox 领取与过期恢复

## 本步交付

`RunRepository.submit/retry/resume` 原有事务已经将 Run 和 `run.dispatch` Outbox 行同时提交。本步新增 `OutboxRepository`，使待派发事件可以被安全领取、确认或延后重试：

- `claim` 按到期时间领取 `pending` 或租约过期的 `sending` 行；MySQL 使用 `SELECT ... FOR UPDATE SKIP LOCKED` 跳过其他派发者锁住的行。短事务只更新数据库，后续发布 Redis 消息应在事务外执行。
- 每次领取递增 `delivery_attempts`，这个次数作为确认/退回时的 fence。旧派发者在新一轮领取后不能确认或退回事件。
- `mark_delivered` 将当前领取标为 `delivered`；`release` 将发送失败的当前领取退回 `pending` 并设定下次可领取时间。事件类型默认只选 `run.dispatch`，测试可使用独立事件类型避免碰开发库中其他待发事件。

MySQL 官方将 [`SKIP LOCKED` 定位为队列表并发访问手段](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)。[Celery 官方任务文档](https://docs.celeryq.dev/en/stable/userguide/tasks.html)指出任务可能重复投递，因此后续消费者仍必须用已有 Run attempt/fence 原子领取保护结果；Outbox 领取本身不提供 exactly-once 模型调用。

## 验证

- 新增 3 项 SQLite 离线测试：领取与确认、租约过期后重领及旧 fence 拒绝、退回延迟和参数边界。第一次执行在缺少模块时失败；实现后 **3 passed**。
- 复用已运行的本机 MySQL，新增定向锁竞争测试：事务 A 锁住一条测试 Outbox 行，事务 B 用 `SKIP LOCKED` 领取同类型另一条，解锁后第一条可再领取，**1 passed in 0.57s**。测试自行清理自己的 Case/Run/Outbox 数据。
- 初次 MySQL 测试误将开发库已有待发事件纳入批量断言；该事件已按原值恢复为 `pending`、`delivery_attempts=0`、`available_at=created_at`，并读回确认。随后给测试自己的两条事件设置唯一事件类型，隔离后通过。
- 后端全量离线回归 **326 passed, 6 skipped**；`ruff check backend evals`、`pip check`、`git diff --check` 通过。跳过项包括显式 opt-in 的真实 MySQL 测试。

## 尚未完成

本步没有向 Redis 发布消息，没有启动或重建 Docker，也没有调用模型；现有 Celery worker 仍只做健康检查。Outbox 中可能有历史 `run.dispatch` 事件，例如手动执行已完成的 Run；下一小步的发布器须跳过不再处于 `queued` 且 attempt 不再活跃的事件。发布成功但数据库确认前崩溃会造成重复投递，消费者必须继续用 `RunRepository.claim` 防重。Linux worker 的数据库连接、共享文件、依赖和模型配置还需单独接线与实测。M3 整体未完成，前端仍在讨论暂停点。

下一小步建议实现 **M3-8b：Outbox 发布器 + Celery Run 任务**，先用脚本化 Provider 验证重复投递及 worker 崩溃后的状态，再安排用户启动/重建 Docker 作真实 MySQL/Redis/worker 验收。
