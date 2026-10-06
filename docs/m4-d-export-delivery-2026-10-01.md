# M4-D：同源导出与工作台交付验收

日期：2026-10-01。范围依据已确认的 [M4 四步计划](m4-large-step-plan-2026-09-30.md)和 [M4-D 执行计划](superpowers/plans/2026-10-01-m4-d-export-acceptance.md)。本记录区分确定性自动化、本地 MySQL/OIDC 浏览器实测和 M5 待验收事项。

## 本步交付

1. `GET /api/v1/agent-runs/{run_id}/export?format=json|xlsx` 先执行与 `/checks` 相同的 Run、快照、原证据和规则复核，再补上本 Case 同一快照的引用来源。OIDC 启用时按 Run 所属 Case 进行读取授权；其他账号查不到该 Run。无结构化结果返回 `409/checks_unavailable`，不生成空报告。响应为附件、禁止内容嗅探和缓存。
2. JSON 的 `schema_version` 为 `1`，`result` 与同一时点 `/checks` 的字段契约一致，包含 Run/Case/快照/资料版本、当前版本标记及每格正式标记；`sources` 给出被引用证据的文件名、PDF 页码或 XLSX 工作表/单元格、原文。XLSX 分为“运行信息”“核验结果”“原文来源”三页，每格保留状态、原因、规则版本、事实和证据 ID、正式标记。历史或部分结果仍可导出，文件和页面均明确显示其非正式性质。
3. 所有可能来自上传资料的 XLSX 值均强制写为文本单元格，禁用公式、超链接和宏。换行统一为 LF，不能进入 XML 的控制字符替换为 U+FFFD，超过 Excel 单格 32,767 字符则拒绝 XLSX 导出；JSON 保留原始证据文本。此措施依据 [OWASP 的公式注入说明](https://community.owasp.org/attacks/CSV_Injection)和 [openpyxl 单元格类型约定](https://openpyxl.readthedocs.io/en/stable/_modules/openpyxl/cell/cell.html)；下载响应使用 [FastAPI 自定义 Response](https://fastapi.tiangolo.com/advanced/custom-response/)。
4. 工作台在选定 Run 的矩阵处提供“导出 JSON / 导出 XLSX”，使用现有登录令牌下载。历史 Run 或含非正式格子时显示文字提醒；Blob URL 稍后释放，给浏览器完成保存动作留出时间。

## 可复现的无模型验收

在已激活的 `docucheck` Conda 环境、项目根目录运行以下命令。它们不访问 DeepSeek；端到端测试在临时 SQLite 中创建空 Case，上传模拟需求、报价、规格，使用与原件哈希对应的 PDF 解析缓存和确定性 `ScriptedProvider`，覆盖完整审查、来源读取、追问、事实修正、历史降级和导出。

```powershell
python.exe -m pytest backend/tests/test_m4_end_to_end.py backend/tests/test_run_export.py -q
python.exe -m pytest backend/tests -q
ruff check backend evals
python.exe -m pip check
cd frontend
npm ci
npm test
npm run build
npm audit --audit-level=high
```

真实 MySQL 定向回归需要本地 Docker MySQL 和已迁移数据库；按 [README 的启动说明](../README.md#本地基础服务)准备后执行：

```powershell
$env:DOCUCHECK_RUN_MYSQL_TESTS = '1'
python.exe -m pytest backend/tests/test_api_workspace_mysql.py backend/tests/test_run_repository_mysql.py backend/tests/test_correction_mysql.py -q
Remove-Item Env:DOCUCHECK_RUN_MYSQL_TESTS
python.exe -m alembic -c backend/alembic.ini current
```

## 面试演示顺序

本地环境依 [README](../README.md#本地基础服务)启动 MySQL、Redis、Keycloak、API、Vue；上传后需要解析进程，完整审查需要 worker 与 dispatcher。演示账号 `alice`、`bob`、`reviewer` 的本地口令只在被忽略的 `.env` 中，不写入演示记录。

1. Alice 登录，创建 Case；按“采购需求”“供应商报价”“供应商规格”上传 `examples/dev-pack-001` 中三份原件，给报价和规格填写同一个供应商与型号。等待三份解析完成。也可在 [M4-C 记录](m4-c-agent-workbench-2026-10-01.md)列出的现有合成 Case 中展示已完成的单家/三家 Run。
2. 选深入模式，提问“请完整审查供应商 A 的八项指标，并列出每项结果与来源”。检查八格状态、Run/快照、当前/正式标记；点“来源”在 PDF 页或 XLSX 单元格核对原文。再在同会话追问一项结果的依据。
3. 在有原文支持的事实集上提交修正并复核；查看新快照的 `needs_review`，切回旧 Run，确认八格及其导出均标记非正式。没有可支持的新值时不要编造修正。
4. 对同一个 Run 分别导出 JSON 和 XLSX；JSON 的 `result` 与 `/checks` 一致，XLSX 的“核验结果”格数与页面一致，“原文来源”可回到原件位置。Bob 直接访问 Alice 的导出 URL 应得到 404。

**外部调用提醒：** worker/dispatcher 若使用 `.env` 中配置的 DeepSeek，步骤 2 的新问答会把问题及必要资料片段发送到模型服务并可能计费。本轮交付验证只使用确定性本地 Provider；不要把下面的合成样例结果说成真实 DeepSeek 页面验收。

## 本轮实测和边界

- 自动化：端到端测试从空 Case 到结构化八格、追问、来源、事实修正、历史导出；导出定向测试检查跨账号 404、空结果 409、JSON 同源、XLSX 三页和公式攻击文本。后端全量 `444 passed, 22 skipped in 68.75s`；真实 MySQL 授权/Run/修正定向 `7 passed`；前端 `12 passed`，`vue-tsc` 与 Vite 构建通过。`python -m ruff check backend evals`、`pip check`、`git diff --check` 均通过；`npm audit --audit-level=high` 报告 0 漏洞。Alembic `current` 为 `0011_document_model_binding (head)`；MySQL、Redis、worker 处于 healthy，Keycloak、dispatcher、cleaner 运行。默认后端测试跳过的 22 项有各自的外部服务开关，不能把全量命令视为全部外部集成验收。
- 本地浏览器：Alice 的既有三供应商合成 Run `5fc280f4827946c9af9ad888f769ce15` 导出 JSON，核对 24 格均正式、41 条去重来源；XLSX 请求返回 200，Blob 为 XLSX MIME、ZIP/PK 文件头、16,710 字节。单家旧 Run `2f54cea2bad64b2cae4e0f66d05017d1` 的 JSON 为 8 格、正式 0、`is_current=false`，页面同时显示非正式警示。后端自动化实际用 openpyxl 打开 XLSX 核对内容；浏览器自动化工具未把 Blob 保存为磁盘文件，因此不声称做过人工打开下载文件的验证。
- 该样例使用已核对哈希的模拟原件和 PDF 中间结果缓存，模型用本地脚本 Provider。实时 MinerU 对新复杂 PDF 的质量、真实 DeepSeek 的成功率/费用/时延、独立封存集正确率、生产身份与对象存储、容量和运维监控均属 M5。当前形态是本机多账号的可审计演示工作台，不是已完成的公网生产发布。
