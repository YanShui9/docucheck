# M3-7 持久化证据驱动单次 Agent Run 设计

## 范围与理由

本步让一个已提交的 `queued` Run 使用同一 Case/快照中已解析入库的证据，执行一次现有八字段 Agent，并通过现有 attempt/fence 契约保存答复。这样先验证上传资料到业务问答的真实连接点，再把同一执行器交给 Celery 调度。只处理单次问题；连续追问的历史恢复、队列投递、进程崩溃恢复、PDF MinerU 实测与前端属于后续步骤。

## 数据流

1. 用 `run_id`、`attempt_id` 从数据库读取问题、模式和绑定的 Case/快照；`RunRepository.claim` 校验活跃 attempt 与 Case 当前版本，原子进入 `running` 并取得 fence。
2. 在一个数据库读取事务中加载该快照的文档、供应商和证据；要求每份文档的解析状态为 `complete` 或 `partial`，完整解析版本存在，证据都能按文档归属。将数据库证据 ID 原样传给现有 `EvidenceRepository`，不重新生成 ID；保持原页/单元格定位及覆盖状态。
3. 用现有 `AgentSession` 和注入的 Provider 执行问题；运行上下文使用持久化的 conversation/run/attempt ID。既有工具预算、来源检查与规则结论继续负责业务判断。
4. 用相同 `run_id`、`attempt_id`、fence 和快照调用 `RunRepository.finish`；取消、替换版本或重试后到达的旧结果由条件更新拒绝。快照未准备好时写入可见的 `partial/snapshot_not_ready`，不调用模型；可用新 attempt 重试。异常退出时尝试标记 `interrupted`，不伪装为正式答复。

## 边界与验收

- 加载器不接受另一个 Case/快照的证据；`pending`、`failed`、缺解析版本、无证据的文档不能用于 Agent。
- 对加载到内存的证据总量设上限，超过时返回容量错误，不启动模型调用。
- 提供本机命令：默认只预览 Run 和资料准备状态；显式 `--send` 才读取已配置 Provider 并发送问题及必要资料片段。命令不启动 Docker 或 Celery。
- SQLite 离线测试覆盖真实上传和解析后的来源 ID、单次 Run 提交、快照未就绪、重复/过期 attempt、异常中断和不发送预览。真实 MySQL/DeepSeek 仍由用户本机显式验收。

## 后续

下一小步把这个执行器接到 Outbox/Celery，并处理重复投递与崩溃恢复。Celery 官方文档要求在延迟确认/重投场景保证任务幂等；本步先让数据库 claim/fence 为重投提供服务端边界。参见 [Celery Tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html) 与 [SQLAlchemy 事务文档](https://docs.sqlalchemy.org/en/20/core/connections.html)。
