# M3-8c Run 失联与队列消息恢复

## 本步实现

1. worker 领取 Run 后，每 10 秒以 `run_id + attempt_id + fence` 条件更新 MySQL 心跳。dispatcher 每 30 秒扫描一次；`running` 超过 120 秒没有心跳时，短事务内锁定并转为 `interrupted`，增加 fence，记录 `worker_lost` 和 `outcome_unknown=true`。旧 worker 的迟到答复不能覆盖恢复结果。恢复器不会自行调用模型；只有用户显式调用已有 retry API 才创建新 attempt。
2. Outbox 已标记为 `delivered`，但对应 Run 仍为当前版本的 `queued` 时，120 秒后重新排队；后续检查间隔按投递次数翻倍，最多 3600 秒。已结束、旧 attempt、旧 Case 版本不会重投。重复 Redis 消息仍由 Run 领取的条件更新拦截，不能保证 broker 的 exactly-once。
3. Redis 使用独立卷和 AOF `appendfsync everysec`；MySQL 仍是权威业务状态。新增 `ix_runs_status_updated` 索引和 `DATETIME(6)` 时间精度迁移，当前 Alembic 版本为 `0003_recovery_precision`。缺少微秒精度时，MySQL 将测试时间四舍五入到下一秒，曾造成刚重排队的事件不能立即领取；真实 MySQL 故障测试先复现，迁移后通过。
4. `GET /api/v1/agent-runs/{run_id}` 增加活跃 attempt 的 `outcome_unknown` 字段，让失联和显式重试后的未知状态可见。已有 `status`、`error_code` 和 retry 接口保持原约定。

## 验证

- 离线后端全量：`350 passed, 8 skipped`；Ruff、SQLite Alembic 升降迁移、Compose 配置检查通过。新增测试覆盖心跳线程在模型阻塞期间运行、超时回收、迟到结果栅栏、Outbox 延时重投、旧版本不重投和 API 可见状态。
- 显式启用的 MySQL/API/Redis/Celery 定向测试：`3 passed`。其中真实 MySQL 测试启动一个不调用模型的独立进程，领取 Run 并写入心跳；强制终止后，以注入的超时时刻触发回收，确认结果未知及旧结果失效。另一空快照 Run 经过真实 Redis/Celery 自动派发，最终 `partial / snapshot_not_ready`、模型调用数 0。
- MySQL `information_schema` 确认 `agent_runs.updated_at`、`outbox.available_at`、`outbox.delivered_at` 为 `datetime(6)`，元数据差异 0。Redis `appendonly=yes`，MySQL、Redis、worker 均健康；故障测试后运行中、排队中和待发的测试任务均为 0。

## 边界与后续

恢复判定依赖数据库与 worker 的时钟、心跳线程和 dispatcher 持续运行。Redis AOF 每秒刷盘仍有短暂丢消息窗口，因此 Outbox 对账保留；若模型调用已发出而进程失联，服务端无法判断服务商是否处理，故只给出 `outcome_unknown` 并要求人工决定是否重试。本步的强制终止测试针对独立 worker 类进程，没有在真实付费模型请求中杀死 Celery 容器，也没有验收断电后的文件系统行为。

下一步应处理跨进程的对话上下文与 `waiting_user/resume` 恢复，以及后台解析任务；前端继续保持设计讨论暂停点。

## 设计依据

- [Celery 任务确认说明](https://docs.celeryq.dev/en/v5.4.0/userguide/tasks.html)：即使启用 `acks_late`，执行任务的子进程被信号终止时仍可能确认消息，所以不能只依赖 broker 重投。
- [Redis 持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)：`appendfsync everysec` 可在灾难性故障时损失约一秒写入，需要保留数据库对账。
- [MySQL 小数秒说明](https://dev.mysql.com/doc/refman/8.4/en/fractional-seconds.html)：未指定 `fsp` 的 `DATETIME` 默认没有小数秒，并对多余精度做舍入；故本步使用 `DATETIME(6)`。
- [MySQL 锁定读取说明](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)：队列式并发扫描可用 `SKIP LOCKED` 跳过已锁定行。
