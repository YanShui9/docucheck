# 上下文与分层记忆执行计划

**Goal:** 在已有资料核验 Agent 上实现可重启恢复、可追溯、有权限边界且预算可控的分层记忆。

**Architecture:** 保留现有 MySQL 消息、Run 和已验证事实。新增有版本的任务状态、旧对话结构摘要与用户明确保存的偏好；上下文装配按必要信息、结构记忆、近期对话的优先级控制预算，旧消息通过当前会话授权的只读工具分页读取。结构摘要只摘录实际对话并保留来源/hash，不推断新的采购事实或保存思维链。

**Tech Stack:** SQLAlchemy Core / Alembic / MySQL，已有 LangGraph / LangChain 消息协议、Pydantic / FastAPI、离线脚本化 Provider。

用户已授权开始下一步，沿用 2026-10-03 的机制设计；本轮不处理 Ollama、前端或动态指标，不新增审批点，不覆盖现有工作区，不提交 Git。

## A. 持久化与写入约束

文件：`backend/app/db/memory_schema_v1.py`、`memory_repository.py`、`models.py`、迁移 `0013_layered_memory.py`、`run_repository.py`、`case_cleanup.py`。

- [x] 先写数据库回归，验证重开文件数据库后仍可加载、跨 actor 偏好不可见、版本冲突、过期/删除和采购事实拒绝。
- [x] 新消息分配会话单调 sequence；既有真实消息按 created_at/id 回填。新字段允许 legacy 测试/导入消息为空，不将空序号误当正式排序证据。
- [x] `memory_profiles` 保存 actor 的修改版本；`memory_records` 保存最多三个有限键：`answer_length`、`answer_format`、`language`。写入必须 confirmed=true 和 expected_revision，删除彻底移除值，profile 版本继续递增，过期记录不注入。
- [x] `task_memory` 绑定 Case/conversation/snapshot/source Run 与原 memory 摘要；只在 fenced Run 的 completed/partial/waiting_user 终态事务中写入，不允许用户通过偏好 API 写事实或改变阈值。
- [x] `conversation_summaries` 保存旧响应阶段的片段与来源；同一 Run 的多次澄清答复分别保留。最近八轮之外最多二十条，摘录 UTF-8 限额、标记截断/省略；版本只能前进。摘要与任务状态在终态事务中生成，回读行数和摘录内容有上限，索引计数仍随会话长度增长，不调用模型。这是首版确定性结构摘要；模型生成语义摘要和异步摘要调度不属于本轮。
- [x] Case 清理先移除任务/摘要，再移除 Run/快照/会话。个人偏好不跟随某个 Case 删除。

## B. 装配、恢复与历史读取

文件：`agent/context.py`、`agent/session_memory.py`、`agent/graph.py`、`agent/tools.py`、`db/conversation_context.py`、`db/run_executor.py`、`db/memory_repository.py`。

- [x] 写回归：超过八轮不丢弃原消息、不会重复注入摘要覆盖的近期消息、源摘要被篡改时不采信、换资料不复用旧业务记忆、历史工具拒绝其他会话/快照。
- [x] session memory v3 保存八轮并额外校验角色/供应商/型号绑定；兼容 v1/v2 四轮存量，验证原文/规则后复用事实与结果。
- [x] 装配先保留系统/工具、本轮完整问题、当前资料版本、焦点/待澄清；偏好、摘要和历史采用独立上限，超预算按整条删除，不截断问题或证据。摘要只作为历史数据，不成为指令或事实证据。
- [x] 按原问题 sequence 限制其他 Run 的历史范围，保留自己的澄清阶段；没有 sequence 的 legacy 回退时间范围。必要时生成覆盖当前近期窗口之外内容的只读摘要视图，保留来源/base_summary_id；普通 partial retry 优先恢复自己的 checkpoint。
- [x] 当前每次输入预算仍取 mode 上限与 Provider 容量减输出预留之小；UTF-8 字节是保守估计，不描述为真实 token。工具调用/返回成对裁剪，已验证结果 handle 单独保留。
- [x] Run 保存 context_manifest：实际读取的来源消息/摘要/任务状态/偏好版本、范围与省略数量，以及历史工具的分页读取/正文指纹；普通重试记录实际恢复 checkpoint；不保存偏好正文、原文或思维链。
- [x] 只读 `read_conversation_history` 支持至多四个当前会话、当前快照的消息 ID 和问题/答复分页。返回历史标签，不产生 evidence_ref；不能用旧答复提交新正式结论。模型不得传 actor、项目或快照覆盖服务器范围。
- [x] 模型/工具调用前后及最终提交检查版本、权限和偏好变更；删除/更新偏好后运行中的旧值不能继续使用或被重新持久化。

## C. API、验证、审查与交付

文件：`api/memory_schemas.py`、`api/main.py`，相应单元/API/生产 Run 集成/opt-in MySQL 测试及交付文档。

- [x] `GET /api/v1/me/memories` 查看本人的有限偏好；`PUT /api/v1/me/memories/{key}` 明确保存；`DELETE` 带 expected_revision 删除。沿用 OIDC；本机兼容模式仍限 loopback，无客户端 actor 参数。
- [x] `GET /api/v1/conversations/{id}/memory` 在现有会话授权范围内返回任务状态、摘要来源与范围/省略标记，不显示其他人的个人偏好。
- [x] 定向测试和完整离线网络禁止测试；Ruff、pip check、Alembic SQLite metadata 比较/升级/降级、可用时真实 MySQL 单独验收；只读独立审查修复遗漏。
- [x] 更新 CLAUDE/README/roadmap 与交付说明。当前会话继承已有项目成员可见性，本轮不声称实现尚未落地的“私有/共享会话选择”；聊天路由、无资料普通聊天、前端记忆入口与动态清单仍按后续模块执行。

## 参考与选择

[LangGraph 官方记忆概念](https://docs.langchain.com/oss/python/concepts/memory)区分会话状态与跨会话记忆；[管理记忆](https://docs.langchain.com/oss/python/langgraph/add-memory)说明裁剪、摘要与持久化。保留既有 MySQL 业务恢复，避免为相同 Run 再建立一套不一致的 checkpoint。

[Anthropic 上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)介绍压缩、结构笔记与按需装配。本轮优先有来源的确定性摘要，适合已有强证据约束；不以“自动推断长期事实”替代采购原文核验。

## 最终验收（2026-10-04）

- 完整离线：893 passed、29 skipped，网络尝试 0；Ruff / pip check / diff 空白检查通过。
- 真实 MySQL：分层记忆两个场景与持久化 Agent 一个场景共 3 passed；head 为 0013_layered_memory。
- SQLite 升级、降级、metadata 对比包含在完整回归并通过；并发首次保存死锁、key 保留字、v3 兼容、澄清/重试/摘要覆盖/用量审计问题已修复后验证。
- 独立只读审查未发现剩余可复现 P0/P1/P2；三个恢复/时间精度/窗口最小复现通过，新增正式回归通过。
- 当前源码已交付，运行 API/Celery 未重启；偏好格式化正式答案和自然语言记忆入口尚未实现，前端及普通聊天/动态清单留待下一模块。
- 实测命令、报告与边界见 [交付说明](../../context-memory-2026-10-04.md)。未操作 Ollama/BGE 或调用真实模型，未提交 Git。
