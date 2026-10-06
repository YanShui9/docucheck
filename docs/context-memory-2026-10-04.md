# 上下文与分层记忆：实现及验收

日期：2026-10-04。范围：聊天式采购助手改造中的**上下文与记忆后端模块**。Ollama/BGE 部署由用户交给另一个 AI；本轮没有操作该部署、调用真实模型或修改前端。

## 1. 这一步做了什么

| 层次 | 本轮实现 | 存储和边界 |
| --- | --- | --- |
| 原始会话 | 保存用户问题、澄清回复及对应的真实助手答复；新消息有会话内单调序号 | MySQL messages、Run 和不可变响应阶段；窗口裁剪不删除原消息 |
| 近期上下文 | 最多八个有答复的阶段，按预算整轮移除较早内容 | 从 SQL 原消息与阶段记录重建；一次 Run 多次澄清可以形成多个阶段 |
| 较旧对话 | 最近窗口之外最多二十条问题/答复摘录，保留消息、阶段 ID 和内容指纹 | conversation_summaries；确定性结构摘要，不是模型生成的语义总结 |
| 任务记忆 | 焦点、待澄清事项和已验证事实/结果的版本化恢复 | task_memory；绑定 Case、会话、资料快照和来源 Run |
| 个人偏好 | 用户明确保存的长度、格式、语言偏好，可查看、过期和删除 | memory_profiles / memory_records；按 OIDC 身份隔离，最多三个有限键 |
| 按需回忆 | 根据原消息 ID 分页读取同会话、同快照的历史问题/答复 | read_conversation_history；每次最多四条，每页正文最多 800 UTF-8 字节 |
| 审计 | 保存装配来源、版本、预算省略、输入指纹与历史工具的实际读取记录 | agent_runs.context_manifest_json；不保存偏好正文或内部思维链 |

重启后恢复的是数据库里的业务状态；本模块没有接入 LangGraph 官方 checkpointer，也没有实现任意图节点继续执行。中断重试沿用既有 attempt/fence 栅栏，重新执行当前问题，并复用通过来源校验的事实和结果。

## 2. 运行过程

1. 消息提交时保存完整输入，在已有 Case 锁下分配会话序号。
2. worker 领取 Run，服务端读取当前身份、权限、快照和问题。澄清恢复或澄清后的失败重试使用最后一条真实回复。
3. 恢复自己已有的阶段；首次执行则恢复当前范围内较早的任务状态。自己的 partial checkpoint 优先，不能被另一轮的旧焦点覆盖。
4. 读取个人偏好、较旧摘要与近期八轮，装配本次上下文。必要信息包括完整当前问题、系统/工具约束、资料清单、焦点和待澄清事项。
5. 超预算先移除可选记忆，再按完整问题/答复对裁剪较早历史；工具调用与返回保持成对。问题和核验原文不能靠截掉单位或限定语适配预算，必要内容放不下时沿用预算失败处理。
6. 模型可以调用历史读取工具了解旧讨论。历史内容带明确标签，不生成 evidence_ref；正式结论仍须依据当前采购原文、已验证事实、规则与覆盖记录。
7. 每次模型/工具操作及最终保存检查权限、快照、记忆来源和偏好是否变化。正式终态事务还锁定个人偏好版本；已删除或过期的旧值不能继续用于后续调用或正式提交。
8. completed、partial、waiting_user 且存在有效 session memory 时，在 Run 终态事务内保存不可变阶段和结构摘要。没有有效 memory 的范围失败不生成新 checkpoint。

输入预算取 mode 限额与 Provider 容量减输出预留中的较小值。当前估算仍为序列化 UTF-8 字节上界，**不等于精确 token 数**；真实 Provider usage 单独保存。

## 3. 恢复与一致性约束

- **范围**：所有任务状态、摘要和历史读取绑定 Case / conversation / snapshot。身份与范围由服务端决定，模型工具没有 actor、项目或快照覆盖参数。
- **顺序**：当前 Run 的原问题序号限制其他 Run 的历史范围，自己的已有澄清阶段可继续读取。避免旧轮重试混入后来问题；不依赖 MySQL DATETIME(0) 的同秒排序。仅没有序号的存量兼容记录回退到时间范围。
- **阶段不可变**：同一 Run 后续澄清、完成或重试不会覆写旧阶段答复。摘要引用阶段 ID，读取时核对原问题 hash、阶段整体 hash，以及相应的当前 Run 状态；旧阶段不错误地与新答复比较。
- **版本**：session memory v3 扩大到八轮，额外核验资料角色、供应商和型号绑定。v1/v2 仍可读取；事实和结果继续经过原文及规则校验。读取结果、修正事实的 API 同步兼容 v3。
- **窗口**：恢复的近期窗口可能与最新保存的摘要不同。此时根据同一授权范围构造只读摘要视图，保留 base_summary_id / 指纹 / 版本，避免澄清推进后出现覆盖缺口或重复注入；不调用模型或偷偷写数据库。
- **摘要容量**：数据库每条摘要最多二十条，问题/答复摘录分别最多 200/240 UTF-8 字节并标记截断；本次注入的摘录序列化内容最多 1,500 字节。省略不表示读过全部旧对话，完整原文仍可分页读取。
- **升级兼容**：既有会话第一次保存时最多回填二十八个旧 completed Run；更多旧原消息仍留在数据库，可按 ID 读取并计入省略数量。回填不能恢复以前已被覆盖且未保存的助手澄清答复。
- **写入栅栏**：checkpoint、摘要和正式 Run 答复同事务提交；失败回滚、旧 attempt、失效 fence 均不能另行覆盖状态。Case 清理先删除摘要/任务记忆，再删除父资源。
- **个人隔离**：偏好键值采用有限词表；不能存入采购阈值、供应商事实或任意提示词。confirmed=true 和 expected_revision 均必需，删除实际移除值，版本继续前进，旧版本不能重新覆盖。并发首次保存的 MySQL 死锁回滚转换为版本冲突，调用方刷新后重新确认。
- **中断审计**：偏好变化导致中断时保留已有模型结果返回的 usage、model_id 和上下文审计，不保存新正式结论。没有返回 usage 的外部故障仍不能据此推断精确账单。

回读和摘要的结果数量有上限；历史阶段总数、省略计数及索引查询成本仍随会话增长。这里没有宣称常数复杂度或完成大规模性能验收。阶段与摘要按 Case 生命周期保留，独立保留期策略属于后续运维工作。

## 4. API 约定

身份沿用当前 OIDC。正常账号按 principal_key 隔离；仅 loopback 的本机兼容模式使用统一 local 身份，不能把该模式视作多账号隔离。

| 接口 | 用途 |
| --- | --- |
| GET /api/v1/me/memories | 查看自己的有效偏好及当前修改版本 |
| PUT /api/v1/me/memories/{key} | 明确保存/更新，携带 value、expected_revision、confirmed=true，可选带时区 expires_at |
| DELETE /api/v1/me/memories/{key}?expected_revision=N | 删除该键，带当前版本 |
| GET /api/v1/conversations/{id}/memory | 按既有项目权限查看当前快照的焦点、待澄清状态及摘要来源；不暴露完整事实缓存或其他人的偏好 |

有限偏好词表：

| 键 | 可选值 | 中文含义 |
| --- | --- | --- |
| answer_length | concise / standard / detailed | 简短 / 标准 / 详细 |
| answer_format | paragraph / bullets / table | 段落 / 列表 / 表格 |
| language | zh-CN / en | 简体中文 / 英文 |

保存示例（body，不包含账号或 API key）：

```json
{"value":"concise","expected_revision":0,"confirmed":true}
```

冲突返回 409；未确认、非法键值、无时区/非未来期限等无效输入返回 422；未登录/越权沿用现有认证、项目资源响应。模型本轮使用了哪些历史页面，会以 message_id / run_id / part / start_char / next_char / text_digest 记录，不抄写页面正文。

## 5. 验证与修复记录

最终稳定源码实测结果：

| 验证 | 结果 |
| --- | --- |
| 完整离线回归，强制禁止网络 | **893 passed、29 skipped，offline_network_attempts=0**，158.59 秒 |
| pip check / Ruff | 均通过 |
| 真实 MySQL：首次偏好并发写入、十轮会话摘要及权限/清理、持久化 Agent | **3 passed**，15.96 秒；Agent 使用脚本化 Provider |
| Alembic 实际 MySQL head | **0013_layered_memory (head)** |
| SQLite 迁移、升级/降级、metadata 对比 | 包含在完整回归中，通过 |
| 稳定源码 diff 空白检查 | 通过 |
| 本轮真实模型调用 | **0** |

29 个跳过项主要为必须显式启用的数据库、Docker/worker、真实解析和 Milvus 集成；本轮单独运行的三项 MySQL 验收不能替代全部外部集成。完整报告保存在 `data/verification/context-memory-final-reviewed-offline.json`，其中包含命令、用时、跳过原因和网络保护结果。

之前的失败报告 `context-memory-final-offline.json` 保留，记录 memory v3 兼容遗漏；中间通过报告 `context-memory-reviewed-offline.json` 为修复后的较早版本，最终结果以本节最新报告为准。真实 MySQL 的迁移失败和首次保存死锁均已修复后重跑，未把失败历史覆盖为成功。

可重复运行以下命令（报告路径需换成未存在的文件名）：

```powershell
python.exe scripts/verify_offline.py --report data/verification/context-memory-recheck.json

$env:DOCUCHECK_RUN_MYSQL_TESTS = '1'
try {
    python.exe -m pytest backend/tests/test_layered_memory_mysql.py backend/tests/test_persisted_agent_run_mysql.py -q
} finally {
    Remove-Item Env:DOCUCHECK_RUN_MYSQL_TESTS
}

python.exe -m alembic -c backend/alembic.ini current
```

新环境需先按既有部署流程准备 MySQL，并在暂停应用写入后执行 `python.exe -m alembic -c backend/alembic.ini upgrade head`。本机这项已执行成功，无需重复做降级。迁移按每页五百条回填旧消息序号，并允许从本次 MySQL 部分 DDL 失败恢复；不存在的旧助手答复不能通过迁移补造。

本轮已修复的实际回归包括：

- 连续澄清丢失上一条用户回复，及完成后无法再次读取澄清回复。
- 失败重试回到原问题，或被较早任务状态覆盖自己的 partial checkpoint。
- 近期窗口推进后，旧摘要与近期窗口之间缺少对话。
- 同秒操作受时间精度影响，遗漏最近阶段或退回旧焦点。
- memory v3 未被结果读取和修正流程接受。
- 历史工具实际读取来源未进入审计、偏好失效中断时漏记已知模型用量。
- MySQL 的 key 保留字导致迁移失败、并发首次偏好写入出现未处理死锁。

只读独立审查已复核上述恢复、窗口、权限、历史证据边界和审计机制，未发现剩余可复现的 P0/P1/P2。测试证明的是所列场景，不等于完成所有生产发布条件。

## 6. 当前产品边界及下一模块

本模块的保存、恢复、预算、历史读取和 API 已写入实际后端。运行中的旧 API/Celery 服务未重启或重建，网页仍显示此前功能。

个人偏好现在能保存并注入决策上下文；**正式核验答案仍由原有中文 renderer 输出，尚不保证按偏好改变长度、格式或语言**。前端查看/保存/忘记入口以及自然语言“记住……”的聊天路由需要后续接入。

会话沿用原项目成员可见性，尚未实现个人私有/项目共享选择；无资料普通聊天、跨会话语义记忆、模板与动态指标不在本模块。Milvus 的业务原文索引与混合检索为此前模块，记忆没有混入采购证据向量集合。

下一模块是**动态审查清单**：允许用户手动设置指标，或从需求/订单提取有原文来源的候选指标，确认版本后再正式核验。它将解决食品等品类无法套用设备八字段的问题；本次不声称该能力已经可用。聊天式前端继续遵守先讨论规范和样式的约定。

## 7. 方案参考

[LangGraph 记忆概念](https://docs.langchain.com/oss/python/concepts/memory)及[记忆管理](https://docs.langchain.com/oss/python/langgraph/add-memory)提供会话状态、跨会话记忆、裁剪与摘要的区分；本项目保留既有 MySQL Run 事务和恢复协议，避免双重状态来源。

[Anthropic 上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)支持按需装配和结构笔记的方向。本轮选择确定性、可追溯摘录，减少额外调用和摘要改写采购事实的风险；语义总结须另行评测。

[MySQL 官方死锁处理说明](https://dev.mysql.com/doc/refman/8.4/en/innodb-deadlocks-handling.html)说明应用需要处理事务回滚。本模块首次保存遇到可识别的死锁时返回版本冲突，不自动重复已确认写入。
