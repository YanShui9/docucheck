# M3 后端阶段验收与审查

日期：2026-09-30。结论：**路线图定义的 M3 本地后端完成条件已满足**。这是合成开发资料及本机服务的功能验收，不等于 M4 工作台或 M5 独立质量、成本、性能验收。

## 本轮补齐

1. **重启后读取会话**：审查发现此前只有创建会话和提交消息的路由，无法在 API 重启后列出已存会话与问题。新增 `GET /api/v1/cases/{case_id}/conversations` 和 `GET /api/v1/conversations/{conversation_id}/messages`，均按创建时间及 ID 稳定排序，使用 `offset`、`limit` 和 `next_offset` 分页。消息返回关联 `run_id`；Agent 答复与状态仍从 `GET /api/v1/agent-runs/{run_id}` 读取。离线 SQLite 和真实 MySQL 分别用新的 Engine/TestClient 验证读取、幂等重放及追问消息关联。
2. **真实 PDF 解析路径**：使用本机 MinerU 可执行文件解析 `examples/dev-pack-001/requirements.pdf`。直接解析约 11.79 秒，返回 12 个证据块、状态 `complete`、无覆盖问题。显式集成测试又走通 API 上传、`parse_background --once`、MySQL 证据与 API 读取；复跑 `1 passed in 12.54s`。
3. **修正快照后台 Agent**：合成 XLSX 原文同时含 `512 GB` 和 `1 TB`。来源事实 512 GB 经同一原文证据修正为 1 TB，复核标为 `needs_review`，旧 Run 失效；新 Run 由隔离 Redis DB 15、真实 Celery worker 和容器内假 OpenAI 接口执行。答复保留“人工复核”，记忆同时保留两个值，模型记录为 `docucheck-smoke-model`、调用 3 次。显式测试复跑 `1 passed in 18.66s`，外部模型调用 0 次。

## M3 完成条件与证据

| 条件 | 验证 |
| --- | --- |
| 重启后会话可读 | `test_api_entry.py` 的新 Engine 重启测试，以及 `test_api_mysql.py` 的真实 MySQL 新 Engine/API 客户端读取；后者在本轮 7 项定向 MySQL 测试中通过。 |
| 澄清释放 worker | `execute_run` 在 `waiting_user` 写入后返回；Celery 任务直接返回执行报告，不保留阻塞等待。`test_persisted_agent_run_mysql.py` 验证等待、API `resume` 与下一 attempt 完成。 |
| 重复消息只提交一次结果 | `test_api_entry.py`、`test_api_mysql.py` 验证同一幂等键返回同一消息/Run，重启后消息列表仍只有一条；`test_run_repository_mysql.py` 验证并发领取与同会话互斥。 |
| 中断后显式重试 | `test_run_recovery_mysql.py` 验证强制终止子进程后的心跳超时回收、旧 fence 拒绝迟到完成；`test_api_entry.py` 验证 `outcome_unknown`、显式 retry 和新 attempt。 |
| 旧结果不成为新版本结论 | `RunRepository.finish` 检查 Case 当前快照和 fence；`test_api_entry.py` 验证旧 Run 的 `is_current=false`，`test_correction_mysql.py` 及本轮 Celery 修正测试验证修正生成新版本、旧结果失效。 |
| 上传到后台结果 | 既有 `test_agent_worker_e2e_mysql.py` 使用真实 MySQL/Redis/Celery 和容器内假模型验证上传 XLSX、后台解析与来源答复；本轮另对真实 MinerU PDF 解析及修正后 Celery Run 做独立验收。 |

## 本轮验证与服务状态

- 最终重跑 `python -m pytest backend/tests -q`：**416 passed, 19 skipped in 93.03s**。跳过项是需显式开启的 Docker、MySQL、真实 Provider 等测试，不计入 416 项。
- 显式 MySQL/API/恢复/修正定向组：**7 passed in 8.20s**。运行期间停止正式 dispatcher，结束后恢复。
- 真实 MinerU 上传解析定向：**1 passed in 12.54s**。修正快照隔离 Celery 定向：**1 passed in 18.66s**。
- `ruff check backend evals`、`pip check`、`git diff --check` 均通过；Alembic `current` 为 `0009_case_cleanup (head)`。
- Compose 的 MySQL、Redis、worker、dispatcher、cleaner 均在运行，MySQL/Redis/worker 为 `healthy`。测试后活跃 Run、待派发 Outbox、待解析文档和待删 Case 均为 0。
- 验收后 Docker Desktop Linux Engine 曾退出，造成一次全量回归极慢；该次运行被中止。随后重启 Docker Desktop，五项服务恢复，队列仍为 0；以上 416/19 是恢复后的完整重跑结果。

## 审查结论与后续边界

- 本轮审查修复了会话历史 API 缺口，并给读接口加入每页最多 100 条的限额，避免无界响应。消息与 Run 保持现有独立存储：读取问题列表后按 `run_id` 取各次状态和答复。
- 后端目前是**本机开发部署**。上传解析进程需按 README 单独启动；若停机，数据库会保留待处理任务，进程重启后继续领取。复杂或超过解析租约的真实 PDF、扫描件和真实大 Case 仍需独立验收。
- 本轮后台模型测试只使用隔离的容器内假接口，不能据此声明修正链路在真实 DeepSeek 上的成功率、时延或费用。单家和三家合成开发包的真实模型业务探针属于此前 M2 记录；正式质量评估在 M5。
- 本地 API 尚无用户认证，不应绑定公网地址。M4 开始实现前按用户要求先讨论前端规范和样式；完整多供应商上传到界面的验收在 M4，独立资料集和性能门槛在 M5。

参考：Celery 对迟到确认任务的[幂等要求](https://docs.celeryq.dev/en/latest/userguide/tasks.html)；FastAPI 的[查询参数约束](https://fastapi.tiangolo.com/tutorial/query-params/)；SQLAlchemy 的 [SELECT 排序与分页 API](https://docs.sqlalchemy.org/en/20/core/selectable.html)。
