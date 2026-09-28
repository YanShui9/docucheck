# M3-2 MySQL 核心数据模型与首版迁移

本步在既有 M3 Run 生命周期规则之后，建立 Case、供应商、输入快照、文档版本、证据块、会话、消息、运行、attempt、事件、Outbox 和幂等记录，共 12 张业务表。建表由 Alembic `0001_core` 迁移执行；模型定义在 `backend/app/db/models.py`。

## 数据边界

- `agent_runs` 绑定同一 Case 的会话、用户消息和输入快照及其 revision；一条用户消息最多对应一个 Run。`run_attempts` 按 Run 编号且不能重复。
- `document_versions` 同时绑定快照和本 Case 内供应商；需求文档无供应商，报价/规格书必须指定供应商。证据块挂在不可变文档版本下。
- `outbox` 绑定具体 attempt，`event_key` 唯一；`idempotency_records` 在操作范围内约束键唯一。Run、attempt 与 Outbox **应由后续 repository 在同一事务写入**；本步尚未实现该事务。
- `agent_runs.active_attempt_id` 目前是运行状态句柄，真正的 claim、fencing、取消与完成仍须后续 repository 以原子条件更新落实。旧快照 Run 可留存作历史，不能作为 Case 当前版本的正式结果。
- 文件正文保存在原件/证据表，原件本身通过相对 `storage_key` 指向本地受控存储。数据库时间字段由服务端写 UTC；首版没有实现上传链路或历史修正。

## 已执行的离线验证

- 测试先于模型与迁移编写，最初因模块/迁移缺失而失败。
- `pytest backend/tests/test_m3_schema.py -q`：5 passed；覆盖同一 Case 运行与 Outbox、跨 Case 快照/供应商拒绝、幂等键唯一、Alembic 临时 SQLite 升降级及模型/迁移差异为零。
- `pytest backend/tests -q`：280 passed；`ruff check backend evals`、`pip check`、`git diff --check` 通过。
- Alembic 在**不连接数据库**的 MySQL 方言下生成建表 SQL，含 12 张业务表及版本表。实际 MySQL 8.4 外键和升级尚需用户验证。

## 用户在本机验证 MySQL

在已激活 `docucheck` Conda 环境、项目根目录执行；本项目的 `.env` 已保存 MySQL 密码，脚本只在本机读取，不需要把密码发给 AI。此操作会在项目现有 `docucheck` 数据库创建上述表。

```powershell
docker compose up -d mysql
docker compose ps mysql
python.exe -m alembic -c backend/alembic.ini upgrade head
python.exe -m alembic -c backend/alembic.ini current
```

预期 `mysql` 状态为 `healthy`，升级无错误，`current` 显示 `0001_core (head)`。若当前库已有同名手工表或迁移失败，请只反馈已脱敏错误和 `current` 输出，不运行 `downgrade base`；该命令会删除本迁移创建的业务表。不要发送 `.env`、数据库密码或原始采购资料。

## 下一步

MySQL 升级验证通过后，下一小步实现 repository 的事务、幂等和原子 Run 状态更新，并做数据库并发测试；随后再接 FastAPI 与 worker。前端仍须先讨论设计规范和样式。
