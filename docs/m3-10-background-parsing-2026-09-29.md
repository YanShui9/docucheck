# M3-10：上传后的后台解析

日期：2026-09-29。本步只处理后端解析派发，不涉及前端，也没有调用 DeepSeek。

## 实现

1. 上传仍立即返回 `pending`。本机 `python -m backend.app.parse_background` 轮询 MySQL，领取当前 Case 快照中的待解析文档；XLSX 直接解析，PDF 使用本机 MinerU 可执行文件。Linux Celery worker 继续负责 Agent Run。
2. 迁移 `0005_background_parsing` 为文档添加领取 ID、租约到期时间和领取次数。数据库事务使用 `FOR UPDATE SKIP LOCKED` 避免并发领取同一文档；解析后按领取 ID 与有效租约条件提交证据和状态。租约过期可重领，旧进程的迟到结果不能入库。连续三次失联后状态转为 `failed`，覆盖问题为 `worker_lost`。
3. Outbox dispatcher 发现 Run 所属快照仍有 `pending` 或 `processing` 文档时延后派发。解析完成或失败后按原有 Agent 逻辑处理；失败文档不会被误判为完整来源。PDF 未配置 MinerU 时记录 `parser_not_configured`；解析异常只记录受控错误码。

## 本机运行

先运行 Alembic 迁移，然后让解析进程持续运行；`--mineru-executable` 是已安装 MinerU 的完整路径，也可在本机 `.env` 设置 `DOCUCHECK_MINERU_EXECUTABLE`：

```powershell
python.exe -m alembic -c backend/alembic.ini upgrade head
python.exe -m backend.app.parse_background --mineru-executable 'E:\developTools\miniconda3\envs\docucheck-mineru\Scripts\mineru-kit.exe'
```

`--once` 可只领取并处理当前最多一份文档，适合排障。进程停止时新上传文档保留 `pending`；进程重启后继续领取。已领取但进程失联的文档在 240 秒租约到期后可重领。此进程目前需要单独启动，尚未配置为 Windows 开机服务。解析结果与错误码可在文档列表 API 查看。

## 验收与限制

- 后端离线 `365 passed, 10 skipped`；`ruff check backend evals`、`pip check`、`git diff --check` 通过。
- 真实 MySQL 定向 `2 passed`，覆盖上传 XLSX 后后台领取、证据入库、重复领取幂等，以及并发事务锁定时跳过锁住的文档；迁移至 `0005_background_parsing (head)`。
- 本机解析进程已启动并持续运行，Docker MySQL、Redis 和 Celery worker 显示 `healthy`，dispatcher 已运行。此状态只代表本次验收时的运行状态，重启电脑后需重新启动解析进程。
- PDF 解析使用缓存 MinerU 中间结果进行离线测试；本步未对真实 MinerU 命令执行整份 PDF 的端到端验收，也未调用模型服务。
- 解析超过 240 秒会失去租约，迟到结果被拒绝，随后可重领；大型 PDF 的耗时、开机自启和人工重新解析失败文档仍需后续完善。

参考：[Celery 任务幂等与重试](https://docs.celeryq.dev/en/stable/userguide/tasks.html)、[SQLAlchemy `SKIP LOCKED` API](https://docs.sqlalchemy.org/en/20/core/selectable.html)。
