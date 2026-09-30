# M3-12c：运行中 cleaner 的删除闭环验收

日期：2026-09-30。本步验证已启动的长驻 `cleaner` 是否能处理 API 发起的删除请求。只使用新建的合成 Case 和开发样例 PDF；不提交 Agent Run，不调用模型，也不涉及前端。

## 验证方式

- 新增显式启用的 `backend/tests/test_case_cleanup_service_mysql.py`。测试通过 FastAPI `TestClient` 创建 Case、上传一份需求 PDF、调用 `DELETE /api/v1/cases/{id}?expected_revision=1`，然后轮询删除状态；`TestClient` 用上下文管理器运行应用生命周期，方式参考 [FastAPI 官方测试文档](https://fastapi.tiangolo.com/advanced/testing-events/)。
- 测试确认上传原件曾存在，后台完成后状态为 `deleted`、原件消失、文档读取返回 410，关联文档、快照、文件清单及上传幂等记录均已清除。成功后仅删除本测试的 Case 墓碑；失败时保留已标记删除的合成 Case 供服务继续清理。
- 默认跳过该测试。只有本机 MySQL 已迁移且长驻 `cleaner` 正在运行时，才设置 `DOCUCHECK_RUN_CLEANER_SERVICE_TESTS=1` 单独执行。此测试不能与会手工删除同一合成 Case 的测试共用测试数据。

在已激活的 `docucheck` Conda 环境中可运行：

```powershell
$env:DOCUCHECK_RUN_CLEANER_SERVICE_TESTS = '1'
python.exe -m pytest backend/tests/test_case_cleanup_service_mysql.py -q
Remove-Item Env:DOCUCHECK_RUN_CLEANER_SERVICE_TESTS
```

## 本机结果

- 指定测试 `1 passed in 3.22s`；`ruff check backend/tests/test_case_cleanup_service_mysql.py` 与 `git diff --check` 通过。
- 长驻服务日志对本测试 Case 记录 `status=deleted`、`files_removed=1`、`files_retained=0`、`error_code=null`。测试后 `pending_deletion_cases=0`、`cleanup_ledger_rows=0`。

## 边界

API 由同进程 `TestClient` 调用，清理确由独立 Docker 容器完成；本步未测 Uvicorn 网络入口、服务重启后恢复、大 Case 清理时延或真实采购资料。共享文件和并发领取此前由 M3-12b 的合成 MySQL 测试覆盖，尚未在长驻服务端到端场景重复验收。
