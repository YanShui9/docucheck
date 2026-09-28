# M3-7 上传证据驱动单次 Agent Run

## 已实现

数据库快照加载器将已解析的 PDF/XLSX 证据转为现有 Agent 使用的 `EvidenceRepository`，沿用持久化的快照 ID 与证据 ID，保留供应商、覆盖状态、PDF 页位置及 XLSX 单元格。`pending`/`failed`、无证据、缺解析版本或总量超限的快照不会进入模型；跨 Case 的快照不可读取。上传资料未显式绑定具体型号时，来源中的 `model` 保持未知，现有规则会保守处理需要型号关联的结论。

单次执行器以现有 `RunRepository.claim` 领取 `queued` attempt，再读取绑定问题和快照，调用已有 LangGraph Agent，最后按同一 attempt、fence 与快照提交状态和答复。模型 ID、用量、结果未知标志保存在 attempt；取消、重试或文件版本变化后的迟到结果不能覆盖当前状态。快照尚未解析时直接记为 `partial/snapshot_not_ready`，模型调用数为零；解析后可通过已有 retry 接口创建新 attempt。进程内异常和 Ctrl+C 会尝试标记 `interrupted`；突然断电或强制终止的恢复属于后续 worker 步骤。

本机入口默认只预览，不领取 Run 或构造 Provider。显式加 `--send` 才读取已配置的模型并发送问题及必要资料片段：

```powershell
python.exe -m backend.app.run_once --run-id <run_id> --attempt-id <attempt_id>
python.exe -m backend.app.run_once --run-id <run_id> --attempt-id <attempt_id> --send
```

`run_id`、`attempt_id` 来自既有消息提交 API 的 `202` 回执。真实发送会消耗模型 API 用量；请先确认快照已完成解析、预览显示 `snapshot_status=ready`，再自行执行 `--send`。输出包含最终答复与模型调用数，不输出密钥。一次 attempt 只可被领取一次；重跑须走已有 retry API。当前只承诺单次问答，连续追问的历史和已核验结果还没有跨进程恢复。

## 验证与限制

- 离线测试将开发包 XLSX 经进程内 HTTP 上传、解析、提交 Run，使用脚本化 Provider 验证来源、正式答复、Run 状态、模型 ID 和用量。PDF 使用受控 MinerU 缓存验证持久化页定位和新旧快照证据隔离。
- 另覆盖未解析快照不调用模型、解析后 retry、容量拒绝、Case 版本变化、重复 attempt、取消后迟到结果、异常/键盘中断，以及模型超时的 `outcome_unknown` 标志。
- 本机后端全量回归：**323 passed, 5 skipped**；`ruff check backend evals`、`pip check` 和 `git diff --check` 通过。**用户本机实际结果（2026-09-28）：** 显式运行 `backend/tests/test_persisted_agent_run_mysql.py -q`，反馈 **1 passed in 3.42s**，随后清除了 `DOCUCHECK_RUN_MYSQL_TESTS`。该测试使用脚本化 Provider，验证真实 MySQL 上的 XLSX 上传、解析、单次 Agent Run、证据来源和结果读取。本轮另执行了网络 API 上传及真实 DeepSeek 来源问答探针，结果见下。
- Celery/Outbox 派发、后台崩溃恢复、持久化多轮上下文与结果轨迹、网络服务及前端仍待实现。M3 整体尚未完成。

### 用户本机真实 MySQL 验收

本次已由用户在已激活 `docucheck` Conda 环境、MySQL 可用时逐行执行；复验命令如下：

```powershell
$env:DOCUCHECK_RUN_MYSQL_TESTS = '1'
python.exe -m pytest backend/tests/test_persisted_agent_run_mysql.py -q
Remove-Item Env:DOCUCHECK_RUN_MYSQL_TESTS
```

本次结果为 **1 passed in 3.42s**。该测试只用脚本化 Provider，验证真实 MySQL 的上传、解析、Run 和证据来源，不调用 DeepSeek；结束时清理自己的测试数据。

### 用户本机真实 DeepSeek 来源问答探针

下面只使用开发包中的 `vendor-a-quote.xlsx`，验证真实模型能否从正式上传证据回答来源问题，不代表八字段完整审查。会在本机 MySQL 和 `data/uploads` 留下一份开发用 Case；`--send` 将问题及必要资料片段发送到 `.env` 中配置的模型服务，可能产生正常 API 费用。不要在终端或反馈中粘贴 API key。

1. 确保 MySQL 已运行；在**第一个**已激活 `docucheck` 环境的 PowerShell 窗口、项目根目录启动本地 API，看到 `Uvicorn running on http://127.0.0.1:8000` 后保持该窗口运行：

   ```powershell
   python.exe -m uvicorn backend.app.api.main:create_app --factory --host 127.0.0.1 --port 8000
   ```

2. 在**第二个**同样已激活环境的 PowerShell 窗口、项目根目录逐行运行。`$uploaded` 应显示 `parse_status: pending`、`revision: 1`；解析命令应显示该文档 `complete`：

   ```powershell
   $base = 'http://127.0.0.1:8000'
   $case = Invoke-RestMethod -Method Post -Uri "$base/api/v1/cases"
   $quotePath = (Resolve-Path -LiteralPath '.\examples\dev-pack-001\vendor-a-quote.xlsx').Path.Replace('\', '/')
   $uploadKey = [guid]::NewGuid().ToString('N')
   $uploadJson = curl.exe --silent --show-error --fail -X POST "$base/api/v1/cases/$($case.id)/documents" -H "Idempotency-Key: $uploadKey" -F 'role=quote' -F 'supplier_handle=vendor-a' -F 'supplier_name=Vendor A' -F 'expected_revision=0' -F "file=@$quotePath"
   if ($LASTEXITCODE -ne 0) { throw '文件上传失败，请反馈脱敏错误' }
   $uploaded = $uploadJson | ConvertFrom-Json
   $uploaded | Select-Object document_id, snapshot_id, revision, parse_status
   python.exe -m backend.app.ingest_snapshot --case-id $case.id --snapshot-id $uploaded.snapshot_id
   ```

3. 创建会话并提交一个来源问题；`$submitted` 应包含 `run_id` 与 `attempt_id`。先运行预览，确认 `snapshot_status` 为 `ready`、`run_status` 为 `queued`：

   ```powershell
   $conversation = Invoke-RestMethod -Method Post -Uri "$base/api/v1/cases/$($case.id)/conversations"
   $question = @{ content = '请列出供应商 A 报价中已装内存的原文和所在单元格'; mode = 'standard'; expected_revision = $uploaded.revision } | ConvertTo-Json -Compress
   $headers = @{ 'Idempotency-Key' = [guid]::NewGuid().ToString('N') }
   $submitted = Invoke-RestMethod -Method Post -Uri "$base/api/v1/conversations/$($conversation.id)/messages" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $question
   $submitted | Select-Object run_id, attempt_id
   python.exe -m backend.app.run_once --run-id $submitted.run_id --attempt-id $submitted.attempt_id
   ```

4. **以下命令实际调用模型。** 只有预览显示 `ready` 时执行；完成后查询数据库中的 Run 状态。期望答复能指出 `vendor-a-quote.xlsx` 的 `报价!B6` 和 `16 GB`，Run 为 `completed`，但真实模型结果须以输出为准：

   ```powershell
   python.exe -m backend.app.run_once --run-id $submitted.run_id --attempt-id $submitted.attempt_id --send
   $run = Invoke-RestMethod -Method Get -Uri "$base/api/v1/agent-runs/$($submitted.run_id)"
   $run | Select-Object status, error_code, answer_text
   ```

同一个 attempt 不要再次执行 `--send`。若返回 `partial`/`failed`，把脱敏的命令输出和 Run 状态发来；后续按现有 retry API 处理。完成后可在第一个窗口按 Ctrl+C 停止本地 API。上传使用 [FastAPI 官方的 File/Form 模式](https://fastapi.tiangolo.com/tutorial/request-files/) 与 [curl `--form` 语法](https://curl.se/docs/manpage.html)。

### 本轮实际探针结果

本机网络 API 的 `/health/ready` 为 `ok`；上传 `vendor-a-quote.xlsx` 后创建的快照 revision 为 1、初始解析状态为 `pending`。解析 CLI 将其标为 `complete`，生成 14 条证据，覆盖问题为空。Run 预览为 `queued/ready`；显式 `--send` 后，真实 `deepseek-flash` Run `04c7167f488b4fde8fa69e9e6576fc62` 为 `completed`、`error_code=null`、`is_current=true`，5 次模型调用，记录 12,732 输入 token、481 输出 token，`outcome_unknown=false`。答复引用 `报价!A6` 的“已装内存”和 `报价!B6` 的“已装内存 16 GB”；API 原文、单元格与持久化证据一致。该单文件来源问答通过；不能据此宣称八字段审查或多轮恢复已通过。

首次真实答复把数据库文档 ID 显示为来源名称。根因是入库快照保留了 `original_name`，但 Agent 答复渲染直接使用内部 `document_id`。随后新增失败复现测试，在仓库中将快照的原文件名作为**显示标签**传给渲染器，内部文档 ID、证据 ID 与校验边界保持不变。针对测试与答复范围测试 **27 passed**；后端全量 **323 passed, 5 skipped**，Ruff 通过；同一份 MySQL 快照重新加载可将该文档 ID 映射到 `vendor-a-quote.xlsx`。旧 Run 的答复按历史记录保留 ID；修正后的真实模型 Run 尚未再次调用。

## 设计依据

数据库状态更新复用 [SQLAlchemy `Engine.begin()` 事务边界](https://docs.sqlalchemy.org/en/20/core/connections.html)。未来将执行器交给队列时，必须按 [Celery 官方任务文档](https://docs.celeryq.dev/en/stable/userguide/tasks.html) 处理重复投递、延迟确认和 worker 丢失；本步的 claim/fence 是服务端拒绝旧 attempt 的基础，尚不代表队列可靠性已验收。
