# M3-5 文件上传与输入快照实施计划

> 用户已授权开始 M3 下一小步。本计划只实现后端上传、文件角色/供应商绑定及不可变快照；解析入库与 Agent worker 留在后续小步。

**目标：** 用户从空 Case 上传 PDF/XLSX 后，取得持久化文档记录和递增的输入版本；旧 Run 继续指向旧快照。

**架构：** FastAPI `multipart/form-data` 接收文件与角色信息，服务层限量写入 `data/uploads/` 的服务端随机路径，校验真实文件结构及哈希。SQLAlchemy 事务锁定 Case、校验 `expected_revision`、复制上一快照的活动文档、替换同角色同供应商的文档，最后写入新快照、版本和幂等回执。文件落盘在事务前；失败时清理本次新文件。

**依赖：** FastAPI 的 `UploadFile` 需要 `python-multipart==0.0.32`；用户在 `docucheck` Conda 环境安装。

---

### Task 1：先验证关键业务行为

- [x] 新增隔离 SQLite HTTP 测试：需求 PDF 与供应商 XLSX 连续上传产生 revision 1、2，第二快照同时包含两者，第一快照仍不变，旧 Run 显示过期。
- [x] 验证同键同载荷重放、异载荷冲突、旧版本拒绝、未知 Case/错误角色绑定拒绝。
- [x] 验证无效 PDF/XLSX、超限文件、路径化原名、超过 7 个活动文档或 30 页 PDF 不留下数据库记录和新文件。

### Task 2：最小实现

- [x] 增加 `backend/app/db/document_repository.py`：快照事务、供应商绑定、文档列表及幂等回执。
- [x] 增加 `backend/app/api/upload.py`：有界写入、SHA-256、PDF/XLSX 内容预检、随机存储名和失败清理。
- [x] 扩展 `backend/app/api/main.py`、`schemas.py`：`POST /api/v1/cases/{case_id}/documents` 与 `GET /api/v1/cases/{case_id}/documents`，沿用统一错误体。
- [x] 锁定 multipart 依赖到 `backend/environment.yml`。

### Task 3：验收与交接

- [x] 定向及后端回归、Ruff、`pip check`、差异检查通过。
- [x] 更新 README、CLAUDE、路线图和 M3-5 验收记录，写明解析/worker 尚未完成，并给用户真实 MySQL 测试步骤。未经用户操作不声称 MySQL 验收。
