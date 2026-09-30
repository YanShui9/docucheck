# M3-12d：网络 API 到长驻 cleaner 的删除闭环

日期：2026-09-30。本步在 M3-12c 的合成 Case 验收测试中加入真实 loopback HTTP 路径，用临时 Uvicorn 进程验证 API 网络入口与独立 Docker `cleaner` 协作。不提交 Agent Run，不调用模型，不处理已有 Case。

## 实现与运行

`backend/tests/test_case_cleanup_service_mysql.py` 默认继续使用进程内 FastAPI `TestClient`。设置 `DOCUCHECK_API_BASE_URL` 后，使用 [HTTPX Client 的 `base_url`](https://www.python-httpx.org/advanced/clients/) 通过网络执行同一组断言；目标仅允许本机 HTTP origin，禁用代理。Uvicorn 使用 [官方文档中的 `--factory`](https://www.uvicorn.org/deployment/) 加载 `create_app`。

启动长驻 `cleaner` 和已迁移的 MySQL 后，在两个已激活 `docucheck` Conda 环境的终端中运行：

```powershell
# 终端 A：测试专用网络入口，结束时按 Ctrl+C
python.exe -m uvicorn backend.app.api.main:create_app --factory --host 127.0.0.1 --port 18080 --no-access-log
```

```powershell
# 终端 B：只创建、上传并删除本测试的合成 Case
$env:DOCUCHECK_RUN_CLEANER_SERVICE_TESTS = '1'
$env:DOCUCHECK_API_BASE_URL = 'http://127.0.0.1:18080'
python.exe -m pytest backend/tests/test_case_cleanup_service_mysql.py -q
Remove-Item Env:DOCUCHECK_API_BASE_URL
Remove-Item Env:DOCUCHECK_RUN_CLEANER_SERVICE_TESTS
```

网络服务和测试须使用同一项目 `data/` 与 MySQL。测试确认健康检查、创建、上传、删除请求、状态轮询、原件与关联记录清理，以及文档读取 410。完成后仅删除自己的合成 Case 墓碑。

## 本机结果与边界

- 修改后进程内路径 `1 passed in 2.83s`；独立 Uvicorn 网络路径最终复测 `1 passed in 3.21s`。网络测试对应 cleaner 日志 `status=deleted`、`files_removed=1`、`files_retained=0`、`error_code=null`。
- Uvicorn 已正常关闭，测试端口 `127.0.0.1:18080` 已释放；测试后 `pending_deletion_cases=0`、`cleanup_ledger_rows=0`。Ruff 和 Git 差异检查通过。
- 这只验证一份合成 PDF 的上传与删除。服务重启后的待删任务恢复、大 Case 时延、真实采购资料、解析及 Agent 链路仍需分别验收。
