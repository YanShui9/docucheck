# M3-5 文件上传与输入快照

## 已实现范围

`POST /api/v1/cases/{case_id}/documents` 接收 `multipart/form-data`：`file`、`role`（`requirement`/`quote`/`spec`）、`expected_revision`，供应商文件还需 `supplier_handle` 和 `supplier_name`；必须传 `Idempotency-Key`。返回 `document_id`、`snapshot_id`、新 `revision`、`parse_status=pending` 与原件 SHA-256。`GET /api/v1/cases/{case_id}/documents` 查询当前快照及文档清单。空 Case 从 revision 0 开始，首次上传变为 1。

上传按服务端生成的随机文件名写入 `data/uploads/`（可用 `DOCUCHECK_DATA_DIR` 指定同一数据根，worker 未来挂载 `/data`）；数据库仅存相对 `storage_key`。每次成功上传在 MySQL 同一事务中锁定 Case、检查预期版本、复制上一快照仍有效的文档、替换同角色同供应商文档、更新 Case revision，并保存幂等回执。旧快照文档记录与原件保留，旧 Run 的 `is_current` 变为 `false`。重复的同键同载荷返回原回执；同键异载荷或旧 revision 返回 409。文件先落盘再写数据库，数据库失败会清理本次文件；进程在两步之间崩溃时可能留下孤儿文件，后续清理任务需回收。

服务端限制单文件 10 MiB、活动文档 7 份、活动供应商 3 家、当前快照 PDF 合计 30 页。只接受 `.pdf`、`.xlsx`；不使用客户端文件名构造路径，不信任客户端 MIME。PDF 检查签名、结构和页数；XLSX 复用现有解析器检查结构与解压/单元格等首版限额。已知 `Content-Length` 超出请求上限时提前拒绝，文件读取过程再次执行硬上限。当前 API 只面向本机单用户；网络部署前仍需请求体限流、认证及权限控制。

## 验证

- 用户在 `docucheck` Conda 环境安装 `python-multipart==0.0.32`；本机环境检查已检测到该版本，`pip check` 返回 `No broken requirements found`。
- 新增上传定向 SQLite HTTP 测试：版本递增、旧快照保留、旧 Run 过期、幂等重放/冲突、文件拒绝与清理、PDF 页数/活动文档/供应商上限。
- 本机离线后端回归：**300 passed, 3 skipped**。跳过项是 2 个既有 MySQL 测试与本步新增的真实 MySQL 上传测试。`ruff check backend evals` 与 `git diff --check` 通过。
- **实际结果（用户本机，2026-09-28）：** 显式启用 MySQL 测试并运行 `backend/tests/test_document_upload_mysql.py -q`，反馈 `1 passed in 2.14s`，随后清除了 `DOCUCHECK_RUN_MYSQL_TESTS`。这验证了测试范围内的上传、快照写入、同键重放与文档读取；未启动网络监听，也未调用模型 API。

### 用户本机真实 MySQL 验收

在已激活 `docucheck` Conda 环境、项目根目录且 MySQL 容器保持健康时，逐行运行：

```powershell
$env:DOCUCHECK_RUN_MYSQL_TESTS = '1'
python.exe -m pytest backend/tests/test_document_upload_mysql.py -q
Remove-Item Env:DOCUCHECK_RUN_MYSQL_TESTS
```

本次实际结果为 `1 passed in 2.14s`。测试通过进程内 HTTP 客户端向已迁移的真实 MySQL 上传样例 PDF，检查版本、同键重放及文档/快照记录；结束时删除测试数据。不调用 DeepSeek，不启动 Uvicorn。以上步骤供后续复验；若复验失败，请反馈脱敏的 pytest 错误与摘要，不发送 `.env` 或密码。

## 设计依据与下一步

上传接口采用 [FastAPI 官方的 `UploadFile` 与表单文件模式](https://fastapi.tiangolo.com/tutorial/request-forms-and-files/)，该模式需 `python-multipart`；文件安全边界参考 [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html) 的扩展名/内容校验、随机存储名、大小限制与网页目录外存储；数据库提交使用 [SQLAlchemy 官方 `Engine.begin()` 事务边界](https://docs.sqlalchemy.org/en/20/core/connections.html)。这些资料是设计参考，实际验收以上述项目测试为准。

本步只建立原件与输入版本，`pending` 文档尚无正式解析证据、索引或模型可用上下文；Agent worker、结果读取、认证、清理任务和前端仍未接线。下一小步应将文档解析与证据入库接入后台任务，再让 Run 按快照读取完整证据。任何前端代码开始前先讨论设计规范和样式。
