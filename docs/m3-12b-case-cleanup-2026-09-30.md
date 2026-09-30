# M3-12b：Case 数据与原件受控清理

日期：2026-09-30。承接 M3-12a 的删除请求。此步实现可重试的数据库和上传原件清理；清理只针对已请求删除的 Case，不调用模型，也不实现前端。

## 实现

- `CaseCleanup` 以 Case 行租约领取待删 Case，采用 `FOR UPDATE SKIP LOCKED` 和 `deletion_claim_id` 栅栏。领取队列有 `ix_cases_cleanup_due` 索引，租约在每次关键写入前重新检查；过期进程不能在文件操作结束后提交旧结果。
- 清理事务先把该 Case 的 `storage_key` 写入 `case_cleanup_files`，再删除证据和文档引用。文件清单保留在数据库中，进程崩溃或文件删除失败后可重试。每个键只处理一次；其他 Case 仍引用时保留原件，最后一个引用删除时才删除。
- 原件路径只允许服务端生成的 `uploads/<两位十六进制>/<32 位十六进制>.(pdf|xlsx)`，并验证路径位于受管目录。路径逃逸或非受管键返回错误，Case 不会被标为 `deleted`。
- 清单处理完成后，按外键顺序删除修正审计、Run、事件、Outbox、消息、快照及供应商等记录，保留 Case 墓碑与删除时间。`GET /api/v1/cases/{case_id}/deletion` 可查询 `active`、`deleting`、`deleted` 与错误码；重复 DELETE 在已完成后返回 200。清理开始后文档、证据和事实读取停止，已完成 Case 的文档读取返回 410。
- `python.exe -m backend.app.cleanup_cases --once --case-id <Case ID>` 可定向执行一次；Compose 增加 `cleaner` 服务供后续长驻运行。

## 验证

- 离线后端 `415 passed, 13 skipped`；包含多快照与跨 Case 共享文件、文件已删但清单未提交时重试、文件删除失败、路径逃逸、租约过期、迟到 Run、定向 CLI、读取状态和 Alembic SQLite 升降/元数据比较。
- MySQL 8.4 已从 `0008_case_deletion_request` 升到 `0009_case_cleanup (head)`。合成 Case 和临时原件测试 `1 passed`，包括同一 Case 的并发领取与跨 Case 共享原件的最终清理。测试数据已清除；事后 `pending_deletion_cases=0`、`cleanup_ledger_rows=0`。
- `ruff check backend evals`、`pip check`、`git diff --check` 通过。本步没有调用 DeepSeek。

## 运行边界

本次没有启动长驻 `cleaner`，因此真实已标记删除的 Case 不会由新服务自动清理；检查时待清理 Case 为 0。启动 `cleaner` 后，今后通过 DELETE 标记的 Case 会被物理清理，原件和历史记录不可恢复。现有长驻 API、worker 和 dispatcher 若未重启，仍可能运行旧代码；本次验收限于当前源码、MySQL 迁移和合成数据定向测试。

**2026-09-30 部署更新：** 经确认后已启动长驻 `cleaner`，`docker compose --profile agent ps cleaner` 显示 `Up`，启动日志无错误；启动后 `pending_deletion_cases=0`、`cleanup_ledger_rows=0`。标准 `--build` 因本机连接 Docker Hub 鉴权地址被拒绝而失败，本机改用已有的 `docucheck-worker:latest` 镜像作为基础，复制当前 `backend/app` 离线构建 `docucheck-cleaner:latest`，确认清理模块可导入后以 `--no-build` 启动。这个本地镜像包含当前代码；后续在其他环境部署或需要更新依赖时，应按 `backend/Dockerfile.worker` 正常重新构建。现在新 DELETE 请求会自动触发物理清理；尚未以真实已删除 Case 测量清理时延。

文件系统与数据库没有跨资源原子事务，因此清单设计允许“文件已删除但数据库标记尚未提交”的重试。长时间清理可能因租约过期中断，下一轮继续；真实大 Case 的清理时延尚未测量。队列锁使用场景参考 [MySQL 8.4 锁定读文档](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)，事务边界参考 [SQLAlchemy 2.0 文档](https://docs.sqlalchemy.org/en/20/core/connections.html)。
