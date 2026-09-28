# M3-7 持久化证据驱动单次 Agent Run 实施计划

**目标：** 已提交的单次 Run 可读取上传快照中的正式证据，调用现有 Agent，按 attempt/fence 保存结果。

**架构：** 新增数据库快照加载器与单次执行器；复用 `EvidenceRepository`、`AgentSession`、`RunRepository`。本机命令默认预览，只有 `--send` 才调用已配置模型。

**技术栈：** SQLAlchemy Core、现有 LangGraph Agent、pytest、FastAPI 进程内测试。

## Task 1：持久化快照证据适配

**文件：** `backend/app/evidence/repository.py`、新建 `backend/app/db/agent_snapshot.py`、`backend/tests/test_persisted_agent_run.py`。

- [x] 先写真实上传与解析后读取数据库证据的失败测试；再覆盖持久化快照 ID、证据 ID、PDF/XLSX 定位、供应商边界和 `partial` 状态。
- [x] 覆盖跨 Case、`pending`/`failed`、缺解析版本、无证据和超容量快照。
- [x] 实现加载器；一次读取事务中校验归属，将 `EvidenceBlock`/`ParseResult` 组装为现有领域对象，使用持久化 ID 构建仓库。
- [x] 运行定向测试，确认未就绪快照不触发模型。

## Task 2：受控单次 Run 执行

**文件：** 新建 `backend/app/db/run_executor.py`、修改 `backend/app/agent/graph.py`、`backend/tests/test_persisted_agent_run.py`。

- [x] 先写 Run 从 `queued` 到 `completed` 的失败测试，核对数据库 run/attempt/snapshot ID、原证据来源和 fence。
- [x] 写快照未就绪、重复/过期 attempt、异常中断与取消后迟到结果的失败测试。
- [x] 实现显式 Run 上下文参数和执行器；先 claim，再加载/调用 Agent，按现有状态机 finish；异常按契约中断。
- [x] 运行定向测试，核对没有重复答复或跨快照引用。

## Task 3：本机入口与交接

**文件：** 新建 `backend/app/run_once.py`，在 `backend/tests/test_persisted_agent_run.py` 测试入口；更新 `CLAUDE.md`、`README.md`、`docs/development-roadmap.md`，新建 M3-7 验收记录。

- [x] 先写默认预览不创建 Provider、不 claim Run 的失败测试；显式 `--send` 使用注入 Provider 的测试。
- [x] 实现 CLI，输出脱敏状态和答复；不在错误日志里显示密钥或原始异常。
- [x] 运行后端全量回归、Ruff、依赖检查和 `git diff --check`；记录真实 MySQL/模型尚未运行的限制。
- [x] 保存代码与文档，给用户提供真实 MySQL 测试和显式模型验收的本机步骤。
