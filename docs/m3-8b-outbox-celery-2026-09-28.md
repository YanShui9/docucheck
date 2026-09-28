# M3-8b Outbox 发布与 Celery Run 任务

## 本步实现

- `OutboxRepository` 的短事务领取后，发布器在事务外发送 `run.dispatch` 到 Redis。发送失败按次数延后，旧 Run、旧 attempt 和已结束 Run 不再投递。发布成功后用领取次数确认；确认丢失时允许重发。
- Linux Celery worker 注册 `docucheck.execute_run`，从 MySQL 读取 Run 和已入库的证据，调用原有 LangGraph 执行器。任务先检查当前 attempt；重复投递不会再次执行已领取的 Run。Provider 配置错误会写入失败状态和安全错误码。
- worker 与 dispatcher 分别运行；只有 worker 获得模型配置。Compose 的 `agent` profile 使自动派发必须显式启用。本机 Conda 和 Linux worker 分别锁定依赖；数据库连接可选择 Docker 内的 `mysql:3306`。
- Celery 使用 JSON、`acks_late`、单任务预取和 Redis 1 小时可见性超时。`task_id` 只用于追踪，不作为去重依据；权威状态始终在 MySQL。

## 验证结果

- TDD 定向测试先因缺少发布器、任务包装或数据库地址配置失败，实现后通过。离线后端全量 **341 passed, 7 skipped**；Ruff、`pip check`、Compose 配置检查通过。默认跳过项包括显式启用的 MySQL/Redis/Celery 集成测试。
- Linux worker 与 dispatcher 镜像构建成功；容器内任务注册、数据库地址、模型配置读取通过。两个服务已启动，worker 显示 `healthy`，并监听 `runs` 队列。
- 启动时有一条历史待发事件，其 Run 已结束；dispatcher 记录 `skipped=1`，没有发给 worker。
- 显式启用的真实 MySQL/Redis/Celery 闭环测试 **1 passed**：测试创建一条空资料快照 Run，Outbox 发布、worker 领取与状态持久化均成功，最终 `partial / snapshot_not_ready`，`model_calls=0`。测试数据已清理；读回待发事件与排队 Run 均为 0。
- 离线脚本化 Provider 测试证实，同一 attempt 的重复 worker 执行只产生第一次的 3 次模型脚本调用；随后返回 `ignored / stale_attempt`。另模拟“消息已发但数据库确认丢失”后重发，Run 原子领取仍只允许一个执行者。

## 边界与下一小步

本步未调用真实模型 API，没有实现后台上传解析任务，也没有实现跨进程的多轮追问状态恢复。进程内异常可把已领取 Run 标为 `interrupted`；如果 worker 在模型调用期间被强制终止，MySQL 的 Run 仍可能停在 `running`。目前没有 Run 心跳/超时回收，也没有对 Redis 消息丢失后已标为 `delivered` 的 Outbox 做对账。当前 Redis 仍是开发用无持久化配置。下一小步 M3-8c 应先加入 **Run 心跳、失联检测与人工重试边界**，再做断电/容器强杀的故障注入；恢复时不能盲目重发可能已经产生未知结果的模型请求。

自动派发已处于运行状态，今后新提交且已解析的 Run 会调用本机 `.env` 中配置的模型服务；停止自动处理可运行 `docker compose --profile agent stop dispatcher worker`。
