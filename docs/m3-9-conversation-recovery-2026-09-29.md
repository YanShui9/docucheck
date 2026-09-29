# M3-9：跨进程会话追问与澄清恢复

日期：2026-09-29。范围是已有持久化 Run 的多轮后端状态，不包含前端、后台解析或真实模型质量验收。

## 实现

1. `agent_runs.memory_json` 保存服务端产生的最近四轮问答、最新的每供应商每字段核验结果、追问焦点、待澄清问题和答复。`run_attempts.resume_message_id` 指向用户的澄清回复。迁移为 `0004_conversation_memory`。
2. 同一会话、同一快照只能有一个 `queued`、`running` 或 `waiting_user` Run。提交事务锁定 Case，使用锁定读取检查活跃 Run；幂等重放保持原有结果。
3. `POST /api/v1/agent-runs/{run_id}/resume` 接收 `content` 与 `expected_revision`，要求 `Idempotency-Key`。仅 `waiting_user` 可恢复，回复入库后创建新 attempt 和 Outbox 记录。同键异回复返回 409。
4. worker 领取 attempt 后，读取同一会话、同一快照的最近已完成 Run。恢复时对核验结果逐项检查快照、供应商、规则版本、证据引用以及资料内容指纹。连续澄清使用保存的最近四轮问答；历史自然语言答复仅供指代，不作为新的采购证据。资料状态变化或内存越界时以 `memory_scope` 部分结果结束，不调用模型。
5. 每次提问仍创建新的 LangGraph 执行。这里持久化的是应用层会话与可复用的领域结果，并非 LangGraph 节点级 checkpoint。现有澄清动作会结束本次图运行，因此不采用 `interrupt()` 恢复同一个节点。参考 [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 与 [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。

## 验收

- 离线后端：`357 passed, 9 skipped`；`ruff check backend evals` 和 `pip check` 通过。
- Alembic SQLite 升降迁移与 metadata 对比通过；本机 Docker MySQL 8.4 已升级至 `0004_conversation_memory (head)`。
- 显式 MySQL 定向测试：Run repository、API、上传证据 Agent Run 共 `5 passed`。含不同幂等键并发提交仅一条成功、真实 MySQL 连续追问、澄清回复恢复。脚本化 Provider，不请求 DeepSeek。
- 操作 MySQL 前暂停了 dispatcher 和 worker；数据库内活跃 Run 与待派发 Outbox 均为 0。重建并恢复服务后，MySQL、Redis、worker 均显示 `healthy`，dispatcher 运行中。

## 边界和后续

- 最近四轮问答用于指代；超过窗口的早期措辞会被淘汰。核验结果按三家八字段最多保存 24 个，每结果最多 256 个证据 ID/事实 ID。
- 没有实现同一图节点的检查点恢复，也没有用真实 DeepSeek 做跨进程多轮质量与费用测量。
- 后台上传解析、资料修正/删除清理、前端交互与认证仍属于 M3 后续。开始前端工作前先与用户讨论规范和样式。
