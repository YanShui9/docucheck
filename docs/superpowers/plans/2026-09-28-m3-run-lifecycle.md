# M3 运行生命周期契约实施计划

> 本计划落实既有 [Agent 架构 v1.0](../specs/2026-09-24-agent-first-design.md) 的运行状态与恢复边界。用户已授权开始 M3；本轮只执行第一步，不进入数据库、Docker 或前端。

**目标：** 让后续 API、MySQL repository 和 worker 共享一套可测试的 Run 状态与 attempt fencing 规则，防止取消或重试后迟到的模型结果覆盖当前运行。

**架构：** `backend/app/runtime/lifecycle.py` 保存纯 Python、不可变的状态转换；持久化层将使用带 `run_id`、`attempt_id`、`fence`、`snapshot_id` 和旧状态条件的原子 SQL 更新。服务端状态与模型临时图状态分离，重试创建新 attempt，不承诺从模型 token 精确续跑。

**技术栈：** Python 3.12 标准库、pytest；本计划不需要模型 API、数据库或容器。

---

### 本轮：Run 状态与 attempt 归属

**文件：** 新建 `backend/app/runtime/lifecycle.py`、`backend/tests/test_run_lifecycle.py`，更新项目状态文档。

- [x] 先写测试：创建 Run 后只能领取一次；`running` 才能提交终态；结果必须匹配当前 `attempt_id`、`fence` 与 `snapshot_id`。
- [x] 执行定向测试并确认因模块缺失而失败。
- [x] 实现 `new_run`、`claim`、`finish`、`interrupt`、`cancel`、`retry`、`resume`；状态为 `queued/running/waiting_user/completed/partial/failed/cancelled/interrupted`。
- [x] 测试取消后的迟到完成、旧 attempt 重试后的迟到完成、澄清恢复、版本变化及重复领取。
- [x] 运行定向测试、后端回归、Ruff 和差异检查：6 项定向测试及后端 275 项测试通过；Ruff、`git diff --check` 通过。

**关键调用契约：** `finish(state, attempt_id=..., fence=..., snapshot_id=..., status=RunStatus.COMPLETED)` 仅在所有归属条件都匹配时返回新状态；否则抛出带稳定 `code` 的 `RunTransitionError`。`retry` 和 `resume` 必须传当前快照及版本，成功时替换活跃 attempt。数据库层之后必须以同样条件做原子更新；纯 Python 检查本身不提供跨进程互斥。

### 后续独立小步

1. **持久化模型与迁移：** 加入 SQLAlchemy 2、Alembic、PyMySQL 的环境锁定；设计 Case、Snapshot、Document、Conversation、Message、Run、Attempt、Event、Outbox 等表及唯一约束。先做离线元数据与迁移测试，再由用户启动 Docker 验证 MySQL 升降迁移。
2. **事务与幂等：** repository 在一笔事务中创建用户消息、Run、Attempt 和 Outbox；同键同载荷返回原结果，同键异载荷返回冲突。使用并发数据库测试验收。
3. **API 与文件：** 接 FastAPI Case、供应商/文档、会话、消息、Run 和证据接口；上传时校验类型/容量，绑定快照，统一错误格式与 revision 检查。
4. **Worker：** Linux Celery 消费 Outbox，原子领取与租约续期、fencing、取消及迟到结果防覆盖；模型调用结果不确定时记录 `outcome_unknown`。由用户按步骤运行 Docker 集成验证。
5. **恢复与修正：** `waiting_user/resume`、`interrupted/retry` 创建新 attempt；保存可复用的同快照结果，修正生成新 revision，旧结果不可成为当前正式结论。完成崩溃、重放与版本失效测试。

**阶段验收：** M3 完成须通过重启后会话可读、澄清释放 worker、重复消息只提交一次、取消后迟到结果不发布、显式重试生成新 attempt、旧快照结果不冒充新版本等端到端检查。本轮的纯状态契约只证明第一组规则，不宣称 M3 完成。

**该步环境记录：** 第一小步实施时 `docucheck` Conda 环境尚未安装 SQLAlchemy/Alembic，因此没有安装依赖或运行 Docker。用户随后为第二小步安装了锁定的持久化依赖；MySQL 迁移验证步骤见 [M3-2 记录](../../m3-2-persistence-schema-2026-09-28.md)。
