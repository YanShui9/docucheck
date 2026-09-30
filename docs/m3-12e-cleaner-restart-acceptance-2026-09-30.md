# M3-12e：停机积压删除的恢复验收

日期：2026-09-30。本步验证 `cleaner` 停止期间发起的 Case 删除，在同一容器重新启动后能够继续完成。只使用新建的合成 Case 和开发样例 PDF，不调用模型或处理已有 Case。

## 验证方法

- 新增显式启用的 `backend/tests/test_case_cleanup_restart_mysql.py`。测试要求开始时待清理 Case 与文件清单均为 0，且 `cleaner` 正在运行。
- 使用 [`docker compose stop cleaner`](https://docs.docker.com/reference/cli/docker/compose/stop/) 暂停服务但保留容器；通过进程内 API 创建 Case、上传合成 PDF 并发起 DELETE。超过正常轮询周期后，确认状态仍为 `deleting` 且原件仍在。
- 使用 [`docker compose start cleaner`](https://docs.docker.com/reference/cli/docker/compose/start/) 启动原容器，在 20 秒内轮询完成状态，核对原件、文档记录与清理清单已清除。测试成功后仅删除自己的 Case 墓碑；即使测试异常，`finally` 也会尝试启动 `cleaner`。

运行前确保本机 MySQL 已迁移、`cleaner` 正在运行且清理队列为空。在已激活的 `docucheck` Conda 环境中执行：

```powershell
$env:DOCUCHECK_RUN_CLEANER_RESTART_TESTS = '1'
python.exe -m pytest backend/tests/test_case_cleanup_restart_mysql.py -q
Remove-Item Env:DOCUCHECK_RUN_CLEANER_RESTART_TESTS
```

## 本机结果与边界

- 定向测试 `1 passed in 11.11s`，Ruff 与 Git 差异检查通过。`cleaner` 日志记录本测试 Case `status=deleted`、`files_removed=1`、`files_retained=0`、`error_code=null`。
- 事后 `cleaner` 重新运行，MySQL、worker、dispatcher 未中断；`pending_deletion_cases=0`、`cleanup_ledger_rows=0`。
- 验证范围是服务正常停止时形成的待处理队列，以及启动后的重新领取。清理已经领取后被强制终止、文件删除与数据库提交之间的崩溃、真实大 Case 时延和完整 Agent 链路仍未由本测试验证。
