# M5 前代码审查、修复与回归

日期：2026-10-01。覆盖 M0–M4 的关键业务与信任边界。本轮完成审查修复，没有开始 M5，也没有调用 DeepSeek。审查前已阅读项目入口、README、路线图和 Git 状态；保留工作区原有 M3/M4 未提交改动。

## 结论

已修复六类可复现问题，新增 15 项后端与 6 项前端回归测试。当前源码可以进入 M5 独立验收。此结论覆盖本地演示与已测试的服务边界；不能据此宣称独立文档质量达标、满足公网负载要求或已通过企业生产验收。

审查沿资料上传、解析、快照、检索/抽取、规则、会话恢复、异步 Run、权限、人工修正及导出的核心链路进行。复现测试先失败、修复后通过；完整回归包含原有的并发、取消、重试、旧租约、来源范围和历史失效测试。本轮没有取得第二位独立审查者的完成报告。

## 已修复的问题

| 问题 | 影响与根因 | 修复与验证 |
| --- | --- | --- |
| 明确指定未知供应商仍可能返回已有供应商结果 | “供应商 D”“A 和 D”“供应商 vendor-d”没有被完整识别为未解析对象，答案范围校验可能接受 A 的结果 | 范围契约增加未解析供应商；Agent 在模型调用前进入澄清，列出可用标识；最终答案再次拒绝该范围。三项拒绝测试及一项零模型调用澄清测试通过 |
| 会话恢复未重算保存的结论 | 只检查快照和证据归属，改动业务状态、原因、事实 ID 或结果 ID 仍能成为下轮可复用结果 | 从经原文校验的事实集重跑规则，完整比较结果；失败时不写入恢复状态。v1 或因容量限制缺失事实集的会话保留问答/焦点，清除可复用结论；新抽取尚未核验时不序列化不匹配的旧结果。九项新回归通过 |
| 人工修正异步请求使用变化后的 Case | 保存修正后切换页面，后续复核可能请求新 Case 的路径，原 Case 留在待复核；旧请求还可能污染新页面 | 同步捕获 Case、Run、事实与页面版本，整个保存/复核流程绑定原资源；仅当前页面接收结果。跨 Case 延迟测试通过 |
| 追问使用最新 Run 的会话而非选定 Run 的会话 | 多会话场景中，用户查看一条 Run 并追问，却使用另一会话的上下文与供应商焦点 | 优先使用选定 Run 的 conversation_id，再按同一快照查找或创建会话。两会话追问测试通过 |
| 上传、新建 Case、刷新资料的旧响应覆盖新选择 | 异步操作返回后直接写入全局选择和错误，用户切换 Case 会被拉回或看到旧错误 | 捕获 project_id、case_id、revision 和 selectionVersion；仅对应页面应用响应/错误；防止重复提交。成功/失败上传、新建 Case、刷新失败共四项测试通过 |
| multipart 解析前没有实际请求体总量限制 | 仅凭 Content-Length 提前拒绝；省略或少报请求头会先把整个超大文件写入临时文件，再由保存层拒绝 | 使用现有 Starlette 的 RequestBodyLimitMiddleware，在接收过程中累计总字节；上传整体上限为 10 MiB + 64 KiB 编码开销，文件本身仍为 10 MiB；统一返回 413/file_limit 和 request_id。两项真实分块 ASGI 测试验证超限后停止读取，未生成快照和原件 |

会话复用和 `/checks` 读取共用 `verify_check_result`，避免两处对同一事实采用不同验证逻辑。人工修正、导出使用现有授权与版本契约。

## 已检查的约束边界

- **数据归属**：OIDC 令牌验证与 owner/editor/viewer 项目权限；Run、会话、证据、事实、原件和导出按所属 Case 授权。越权资源不返回内容。兼容本机模式只适用于 loopback 调试，代理或共享部署必须开启 OIDC。
- **业务判定**：处理失败/部分完成与业务结论分离；未完成全量适用证据扫描不得把字段视为不存在或生成正式“满足”。型号、单位、计价、税口径和期限起算事件不明确时要求人工确认。
- **资料覆盖**：首版只支持可提取文本的 PDF 与普通 XLSX；图片、复杂结构、隐藏内容等不能证明覆盖时保守返回 partial。一次 1 份需求、最多 3 家供应商各 2 份资料；单文件 10 MiB、总 PDF 30 页，另有 XLSX 解压/单元格限额。
- **Agent 工具**：模型提出工具动作与候选事实；服务端验证参数、来源和答案句柄，确定性规则形成判定。文档内容不是操作授权，不允许模型替换 Case/快照或直接覆盖业务结论。
- **上下文**：最近四轮问答、有界事实/结果与焦点按快照恢复；事实与结果重新验证。standard/thorough 对应预算与处理深度；过程记录包含工具、覆盖和用量，不宣称持久化模型隐含思维过程。
- **异步与恢复**：Run 与 Outbox 同事务；领取租约、attempt/fence、取消、重试、澄清释放与旧响应拒绝。解析领取和 Case 清理也使用独立租约；删除请求阻止后续写入，共享原件按引用保留，清理错误延期重试。
- **人工修正**：只修正有同一原文支持的提取事实；保存形成新版本，旧结果降为历史。原文同时支持不同值时保留冲突并标记 needs_review；不能人工填写“满足”绕过规则。
- **导出**：JSON/XLSX 与结构化核验结果及同一快照证据绑定；历史/部分结果保持非正式标识。XLSX 使用文本单元格，避免上传原文成为公式。

## 验证结果

| 验证 | 本轮实测 |
| --- | --- |
| 修复前后端基线 | 444 passed, 22 skipped |
| 修复后后端全量 | **459 passed, 22 skipped**，89.60 秒 |
| 显式 MySQL 集成回归 | **13 passed**，10.50 秒；涉及 Run/API、上传、解析、持久化 Agent、多轮恢复、修正、共享原件清理及项目隔离 |
| 前端 | **18 passed**；vue-tsc 与 Vite build 成功 |
| 静态与依赖 | `ruff check backend/app backend/tests evals` 通过；本机与新 worker 的 pip check 均无依赖冲突；npm audit 为 0 个已报告漏洞 |
| MySQL 迁移 | `0011_document_model_binding (head)`；alembic check 未发现新升级操作 |
| 合成 Agent 开发集 | 任务 2/2、可见状态 32/32、引用错误 0/76 |
| 合成检索开发集 | recall@12 17/17，供应商范围检查 9/9 |

22 个默认跳过项为外部环境/显式验收开关控制的测试，本轮另运行了上述 MySQL 子集。Redis/Celery 故障注入、真实 MinerU 与真实模型等外部链路没有在本轮全部重新执行；此前记录仍为各自日期的验证。

两份开发集报告保存在被忽略的 `data/pre-m5-review/m2-agent.json` 和 `m2-retrieval.json`，可用下列命令重建。脚本 Provider 的用量和延迟不能作为真实模型指标；这些开发资料已参与实现，不能充当 M5 封存测试集。

```powershell
python.exe -m pytest backend/tests -q
python.exe -m ruff check backend/app backend/tests evals
python.exe -m pip check
python.exe -m alembic -c backend/alembic.ini current
python.exe -m alembic -c backend/alembic.ini check
python.exe -m evals.m2_agent --output data/pre-m5-review/m2-agent.json
python.exe -m evals.m2_development --output data/pre-m5-review/m2-retrieval.json
```

```powershell
$env:DOCUCHECK_RUN_MYSQL_TESTS = '1'
try {
    python.exe -m pytest backend/tests/test_run_repository_mysql.py backend/tests/test_api_mysql.py backend/tests/test_document_upload_mysql.py backend/tests/test_document_ingestion_mysql.py backend/tests/test_persisted_agent_run_mysql.py backend/tests/test_correction_mysql.py backend/tests/test_case_cleanup_mysql.py backend/tests/test_api_workspace_mysql.py backend/tests/test_background_parsing_mysql.py -q
} finally {
    Remove-Item Env:DOCUCHECK_RUN_MYSQL_TESTS
}
```

显式 MySQL 测试使用独立合成 Case 和假模型。若环境中已有自动派发/清理服务，先暂时停止 dispatcher/cleaner，避免它们领取测试资源，测试后恢复。本轮已恢复服务，并重建 worker/dispatcher/cleaner 镜像；worker 中五个相关源码文件的 SHA-256 与本机一致。MySQL、Redis、worker 健康，dispatcher/cleaner 运行；本地 API 已重启到当前源码，仍使用 OIDC，仅监听 127.0.0.1:8000。前端服务保留原 127.0.0.1:5173。

```powershell
cd frontend
npm test
npm run build
npm audit --audit-level=high
```

## 企业部署前仍需完成的工作

以下是当前实际未验收或未实现项，不因通过单元测试而消失：

1. **质量与性能证据**：独立封存集、真实新 PDF 的 MinerU 处理、DeepSeek 多次运行、失败分母、危险误判、真实费用与 P95/端到端时延。三家样例内容相似，不能代表真实供应商资料的多样性。
2. **规模边界**：项目/Case 列表仍未分页；目前没有用户/组织级存储、并发和费用配额。大量项目/历史资料或多人高并发部署前要补分页、配额与压力测试。
3. **运维与发布**：可复现 CI、构建产物/依赖锁审计、统一脱敏日志与告警、数据库/原件的联合备份和恢复演练、保留周期、HTTPS/请求读取超时、SSO 运维与密钥轮换。当前 Docker Compose 与 Keycloak dev 演示配置不是公网生产配置。
4. **独立复审与新数据**：本轮是核心链路审查与自动回归，不是渗透测试。正式发布仍需第二人复审和独立资料验收；供应商自然语言名称/别名解析应以绑定标识为准，不能宣称任意名称理解已覆盖。

建议 M5 使用三个大步骤：先定稿并封存独立样本、标注与评测口径；再显式进行真实模型/解析/成本/性能验收；最后补 CI、复现及面试交付材料，并按实测决定部署加固范围。本轮止于审查修复，不执行这三个步骤。

## 官方资料参考

- [Vue watcher 的异步失效与清理说明](https://vuejs.org/guide/essentials/watchers.html)：本项目使用页面版本令牌处理迟到响应，保证已提交写操作仍在原资源上完成。
- [Starlette 请求体限流与 ASGI middleware](https://www.starlette.io/middleware/)：核对安装版本 1.7.0 的实际源码后复用其官方组件，验证无长度/少报长度、临时文件解析与统一错误响应。
- [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)：按每次请求检查资源授权，核对嵌套资源不能仅凭 ID 放行。
