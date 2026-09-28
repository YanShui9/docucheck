# M3-6 上传资料解析与证据入库

## 本步交付

已上传文档可由本机命令按 Case 与输入快照解析。服务先验证数据库中的相对存储路径、文件大小和原件 SHA-256，再复用现有 PDF/XLSX 解析器。解析结束后再核对一次原件哈希；文档状态、解析覆盖信息和证据块在同一数据库事务内写入。相同文档重复处理直接返回原回执，证据不会重复插入。失败或部分覆盖不会被伪装成完整业务结论。

证据保留 PDF 页序号、bbox、页面尺寸，或 XLSX 工作表、单元格地址。新增只读接口：

| 用途 | 路由 |
| --- | --- |
| 按文档分页列证据 | `GET /api/v1/cases/{case_id}/snapshots/{snapshot_id}/documents/{document_id}/evidence?after_ordinal=-1&limit=100` |
| 读取单条证据 | `GET /api/v1/cases/{case_id}/snapshots/{snapshot_id}/evidence/{evidence_id}` |

服务端校验证据所属 Case 与快照；`limit` 上限为 100。当前 API 仍按本机单用户设计，部署为多用户网络服务前需要认证与权限控制。解析器版本完整文本放在 `coverage_json.parser_version_full`；现有 `parser_version VARCHAR(64)` 保存版本文本的哈希标识，因此本步无需修改迁移。

本机处理命令：

```powershell
python.exe -m backend.app.ingest_snapshot --case-id <case_id> --snapshot-id <snapshot_id>
```

XLSX 可直接处理。PDF 处理需要配置 `DOCUCHECK_MINERU_EXECUTABLE`，或给命令加 `--mineru-executable <本机程序路径>`；未配置时 PDF 保持 `pending`，命令返回配置错误。该命令会读取已配置的 MySQL 和上传数据目录，不发送资料到 DeepSeek。处理结果为 `complete` 或 `partial` 时返回码 0；有文档 `failed` 时返回码 1；配置或查找错误返回码 2。

## 验证与限制

- 本机离线后端回归：**306 passed, 4 skipped**。跳过项包含需显式启用的真实 MySQL 测试；`ruff check backend evals`、`pip check` 和 `git diff --check` 通过。
- 真实上传的 XLSX 经进程内 HTTP 上传并解析后，可按工作表及单元格查原文；重复处理不增加证据行。开发包 PDF 用受控 MinerU 缓存结果测试页定位、页面尺寸、`partial` 覆盖和新快照独立证据 ID。
- 哈希篡改、路径越界、解析期间原件变化均不能生成证据；注入证据主键冲突时文档状态与证据行一起回滚。跨 Case/快照读证据返回 404。
- **实际结果（用户本机，2026-09-28）：** 显式启用真实 MySQL 测试并运行 `backend/tests/test_document_ingestion_mysql.py -q`，反馈 **1 passed in 2.28s**，随后清除了 `DOCUCHECK_RUN_MYSQL_TESTS`。测试覆盖真实 MySQL 中的 XLSX 上传、解析、单元格来源、重复处理和 Case 隔离。未运行本机真实 MinerU 命令；PDF 结果只通过现有解析器与受控缓存验证，生产资料的准确率、耗时和恢复行为尚未验收。
- 尚未接入 Celery、Run/Agent 的取证流程，也没有异步调度、索引更新、认证或前端。解析输出设有单块 60,000 UTF-8 字节、最多 25,000 块、合计 50 MiB 的入库边界，超出时文档标为 `failed` 并保留原因；后续需要对极端大文档再做性能测试。

### 用户本机 MySQL 集成验收

本次已由用户在已激活 `docucheck` Conda 环境、MySQL 可用时逐行运行；复验命令如下：

```powershell
$env:DOCUCHECK_RUN_MYSQL_TESTS = '1'
python.exe -m pytest backend/tests/test_document_ingestion_mysql.py -q
Remove-Item Env:DOCUCHECK_RUN_MYSQL_TESTS
```

本次结果为 **1 passed in 2.28s**。该测试在真实 MySQL 上上传并解析开发包 XLSX，核对位置、幂等与 Case 隔离，最后清理自己的测试数据；它不验证真实 MinerU PDF 或 Agent worker。

## 依据与下一步

数据库提交采用 [SQLAlchemy `Engine.begin()` 事务边界](https://docs.sqlalchemy.org/en/20/core/connections.html)，让状态和证据共同提交或回滚。下一小步拟将解析工作和现有 Run 生命周期接入 Linux 后台任务，并验证重复投递、进程中断和重试；[Celery 官方任务指南](https://docs.celeryq.dev/en/stable/userguide/tasks.html)强调任务的幂等与重试边界。是否采用具体队列流程，以该步的设计和测试结果为准。任何前端代码开始前，先和用户讨论设计规范与样式。
