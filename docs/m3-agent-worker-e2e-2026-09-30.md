# M3：上传解析至 Celery Agent Run 的隔离闭环

日期：2026-09-30。本步用开发样例 XLSX 和容器内假 OpenAI 接口验收实际 MySQL、Redis、Celery 路径，不向 DeepSeek 或其他外部模型服务发送请求。

## 验收路径

`backend/tests/test_agent_worker_e2e_mysql.py` 是显式启用的集成测试。它要求正式 dispatcher、cleaner、MySQL、Redis 已运行，且没有待处理 Run、Outbox 或文档；运行期间短暂停止正式 dispatcher，使用 Redis DB 15 中的独立派发器和临时 Celery worker。临时 worker 的模型地址仅为自身容器的 `127.0.0.1:18742`，API key 为测试值；假接口由 `backend/tests/support/mock_model_worker.py` 提供。正式 worker 继续监听原 Redis DB 0，不会领取测试任务。

1. 通过 FastAPI 路由创建 Case、上传 `vendor-a-quote.xlsx`、创建会话并提交来源问题；先保留文档 `pending`。
2. 启动隔离 dispatcher，确认 Run 仍为 `queued`、Outbox 仍为 `pending`，没有在解析前派发。
3. 在独立 Python 进程运行后台解析 CLI，确认报价表 `B6` 的证据已入库。
4. 启动隔离 Linux Celery worker，由 dispatcher 经 Redis DB 15 自动派发；容器内假模型按 `search_evidence → read_source → submit_answer` 返回工具调用。
5. 从 Run API 核对 `completed`、文件名、`B6`、`16 GB`；从 MySQL attempt 核对模型 ID 为 `docucheck-smoke-model`、调用次数为 3。最后请求删除测试 Case，等待长驻 cleaner 清理，并恢复正式 dispatcher。

执行命令（已激活项目 Conda 环境、Docker 服务运行时）：

```powershell
$env:DOCUCHECK_RUN_AGENT_WORKER_E2E = '1'
python.exe -m pytest backend/tests/test_agent_worker_e2e_mysql.py -q
Remove-Item Env:DOCUCHECK_RUN_AGENT_WORKER_E2E
```

测试受显式环境变量保护，普通 `pytest backend/tests` 会跳过。它会操作本地 Docker 服务，运行前不应有其他待派发任务。Celery 官方文档指出 eager 模式与真实 worker 行为存在差异；因此本验收使用了[实际 Celery worker](https://docs.celeryq.dev/en/stable/userguide/testing.html)。

## 本机结果

- 第一次运行的业务路径已 `completed`，但测试即时读取 Docker 日志没有数到假接口请求，导致一个观测断言失败；改用 MySQL 持久化的 `usage_json.model_calls` 作为权威次数后重跑通过。另一次重跑发现 Redis DB 15 留有 Celery 队列绑定键，测试预检现只允许这两种绑定键，不会清空该库。加强异常清理后最终定向测试 **1 passed in 28.18s**。
- `ruff check` 通过。结束后仅有原来的五个 Compose 服务在运行；worker `healthy`，正式 dispatcher 已恢复。数据库的活跃 Run、待派发 Outbox、待解析文档和待删除 Case 均为 0。
- 测试没有改动生产业务代码，也没有调用真实模型。它验证的是单个报价 XLSX 的来源问答；修正快照经 Celery worker 的业务审查、真实 MinerU PDF、真实多供应商完整审查、独立资料集质量与时延仍待验收。
