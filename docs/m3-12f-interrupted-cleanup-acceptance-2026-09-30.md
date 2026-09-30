# M3-12f：文件删除后异常退出的清理恢复

日期：2026-09-30。本步验证清理进程在“文件已删除、文件清单尚未标记完成”这一窗口异常退出后，长驻 `cleaner` 能在租约到期后接手。只使用新建的合成 Case 和开发样例 PDF，不调用模型或处理已有 Case。

## 验证方式

- 在 `backend/tests/test_case_cleanup_restart_mysql.py` 新增显式启用的故障验收测试。要求开始时 MySQL 的待清理 Case 与文件清单均为 0，且长驻 `cleaner` 正在运行。
- 测试暂停 `cleaner`，用 API 创建合成 Case、上传 PDF 并发起 DELETE。独立 Python 子进程取得 30 秒数据库租约，将上传原件记入持久清单、删去文档引用及原件文件，随后调用 `os._exit(17)` 异常退出，故意不提交清单完成状态。
- 父进程检查文件已不存在、清单仍未处理、旧租约仍有效；启动 Docker `cleaner` 后确认租约未到期时状态仍为 `deleting`。租约到期后等待重新领取，验证状态变为 `deleted`、领取次数为 2、清单被清除。成功后仅删除本测试的 Case 墓碑；异常路径也会尝试启动 `cleaner`。

在已激活的 `docucheck` Conda 环境中，MySQL 健康、`cleaner` 运行且队列为空时可单独执行：

```powershell
$env:DOCUCHECK_RUN_CLEANER_CRASH_TESTS = '1'
python.exe -m pytest backend/tests/test_case_cleanup_restart_mysql.py::test_crashed_unlink_is_reclaimed_after_lease_expiry -q
Remove-Item Env:DOCUCHECK_RUN_CLEANER_CRASH_TESTS
```

## 本机结果与边界

- 加强租约及领取次数断言后，定向测试 `1 passed in 39.14s`，Ruff 与 Git 差异检查通过。长驻服务对本测试 Case 记录 `status=deleted`、`files_removed=0`、`error_code=null`；文件由异常退出的子进程提前删除，因此续处理阶段删除文件数为 0。
- 测试后 `cleaner`、MySQL、worker 和 dispatcher 运行，`pending_deletion_cases=0`、`cleanup_ledger_rows=0`。
- 测试开始前 Docker Desktop 曾意外处于 `stopped`；用官方 [`docker desktop restart`](https://docs.docker.com/reference/cli/docker/desktop/restart/) 恢复。重启初期 `cleaner` 有一条数据库暂不可用日志，随后正常处理了本测试任务。
- 这是独立 Python 清理子进程在精确故障窗口异常退出并由 Docker `cleaner` 接管的集成测试；没有直接强杀正在执行的 Docker 容器。多文件批次、大 Case 时延、真实采购资料和 Agent 链路仍需另行验收。
