# M3-3 Run repository 实施计划

> 本小步落实已确认的 [Agent 架构](../specs/2026-09-24-agent-first-design.md) 与 [Run 生命周期契约](2026-09-28-m3-run-lifecycle.md)；按用户要求一次只实施一个 M3 小步。先写失败测试，再实现，再验收。

**目标：** 在 MySQL 可执行的事务中创建消息、Run、Attempt、事件、Outbox 与幂等记录，并以条件更新阻止重复领取、迟到提交及旧快照重试。

**架构：** `backend/app/db/run_repository.py` 只负责持久化 Run 事务与状态转换；现有纯 Python 生命周期模块继续定义允许的状态。新问题及 retry/resume 的幂等记录先占各自范围的唯一键，同键同载荷返回原 Run/Attempt 回执，同键异载荷报冲突。领取、完成、取消、重试与恢复使用带旧状态、attempt、fence 和快照条件的 SQL 更新；更新行数必须为 1，相关 Attempt/事件/Outbox 与它在同一事务提交。

**技术栈：** Python 3.12、SQLAlchemy Core 2.0、MySQL 8.4、pytest；离线功能测试使用 SQLite，MySQL 并发测试由用户在本机显式启用。

---

### Task 1：提交事务与幂等

**文件：** 新建 `backend/tests/test_run_repository.py`、`backend/app/db/run_repository.py`。

- [x] 测试：同键同载荷只产生一条用户消息、Run、Attempt、Outbox；同键异载荷报 `idempotency_conflict`；错误 revision 或跨 Case 会话不写入半成品。
- [x] 运行定向测试，确认因 repository 尚不存在而失败。
- [x] 实现 `RunRepository.submit`：校验请求、先插入唯一幂等键，再锁 Case 校验 revision、快照和会话，随后插入用户消息、Run、Attempt、初始事件及 Outbox；并发唯一键冲突只在重新读取到同一键时转为幂等返回。

### Task 2：原子状态转换

**文件：** 扩充 `backend/tests/test_run_repository.py`、`backend/app/db/run_repository.py`。

- [x] 测试：仅一次领取可成功；取消后的完成、旧 fence、旧 attempt、旧快照均不能写答案；显式 retry/resume 创建新 Attempt 和 Outbox；Case 版本改变时拒绝重试。
- [x] 运行定向测试，确认新增测试失败。
- [x] 实现 `claim`、`finish`、`cancel`、`interrupt`、`retry`、`resume`；retry/resume 同样使用幂等键，所有竞争状态写入都用 SQL 条件更新并检查 `rowcount == 1`，事件按 Run 顺序递增。

### Task 3：MySQL 并发验收与收尾

**文件：** 新建 `backend/tests/test_run_repository_mysql.py`，更新 `CLAUDE.md`、`README.md`、`docs/development-roadmap.md`、本计划。

- [x] 编写仅显式启用的真实 MySQL 竞争测试：同幂等键并发提交只建一条 Run；同 Attempt 并发领取只成功一次；取消与完成竞争最多一个终态生效。使用随机 ID 测试数据并清理。
- [x] 本机执行离线定向测试、全后端测试、Ruff、依赖检查与差异检查；真实 MySQL 测试仍待用户执行。
- [x] 用户回传真实 MySQL 并发结果：`1 passed in 0.92s`；已记录验收，本次未发现需修正的问题。

**边界：** 本步不调用模型 API、不启动 Docker、不写前端。MySQL 集成测试默认跳过，不把 SQLite 测试称为跨进程并发验收。外部模型结果未知窗口、worker 租约和最终 HTTP 错误映射留给后续步骤。
