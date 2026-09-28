# M3-3 Run repository：事务、幂等与条件更新

## 本步实现

`backend/app/db/run_repository.py` 新增 SQLAlchemy Core repository。`submit` 在同一事务中写入幂等键、用户消息、Run、首个 Attempt、状态事件和待派发 Outbox；任何中途异常回滚。幂等范围为 Case 的消息提交操作：同键同载荷返回首条 Run 的原始回执，同键异载荷报 `idempotency_conflict`。新请求锁定 Case 行，核对当前 revision、输入快照与会话归属。

`claim`、`finish`、`interrupt`、`cancel`、`retry`、`resume` 使用带旧状态、活跃 attempt、fence、快照/版本条件的 SQL 更新，并要求恰好更新一行。领取和提交答案前检查 Case 当前 revision；输入变更后，旧 Run 不再领取或发布答案。重试/澄清恢复以各自操作范围的幂等键生成新 Attempt、事件和 Outbox，同键重放返回原 Attempt ID；不复用旧 attempt。事件与状态改动同事务提交。

## 已执行的离线验证

- 先运行新测试，因 `backend.app.db.run_repository` 不存在而失败；新增 Case 版本测试和 retry/resume 幂等测试也分别先失败再修正；完成实现后，定向测试 **13 passed**。
- `pytest backend/tests -q`：**293 passed, 1 skipped**。跳过的 1 项为默认关闭的真实 MySQL 并发测试。
- `ruff check backend evals`、`pip check`、`git diff --check` 通过。
- 离线测试覆盖新问题与 retry/resume 同键重放/冲突、错误版本与跨 Case 回滚、重复领取、旧 fence/快照拒绝、取消后的迟到答案、重试/恢复以及 Case 更新后的旧 Run 拒绝。SQLite 测试不能代替 MySQL 跨连接竞争验收。

## 用户在本机验证 MySQL 并发

在已激活 `docucheck` Conda 环境、项目根目录，且 MySQL 容器仍为 `healthy` 时执行。测试读取本机 `.env` 的数据库密码；**不要发送密码或 `.env`**。测试会在现有开发数据库中插入随机 ID 的临时 Case/Run，结束时按外键顺序清理。请先确认这个数据库允许测试写入。

```powershell
$env:DOCUCHECK_RUN_MYSQL_TESTS = '1'
python.exe -m pytest backend/tests/test_run_repository_mysql.py -q
Remove-Item Env:DOCUCHECK_RUN_MYSQL_TESTS
```

预期 `1 passed`。该测试用两个连接竞争同一新问题幂等键、同一 Run 的领取、完成与取消的终态，以及同一 retry 幂等键；要求消息/Run 仅创建一次、领取只成功一次、终态只能有一个、重试只创建一个新 Attempt。若失败，请回传脱敏 traceback 和测试摘要，不发送数据库凭据。

## 范围边界与下一步

真实 MySQL 并发测试尚待用户运行，因此当前只完成代码与离线验收。repository 尚未接 FastAPI、文件上传或 Celery worker；也没有 worker 租约续期、外部模型结果未知窗口的完整处理。下一小步在 MySQL 并发结果通过后再接正式 API/worker 路径。前端工作开始前先讨论设计规范和样式。
