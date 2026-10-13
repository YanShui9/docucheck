# DocuCheck 当前开发上下文

入口只保留有效约定。当前状态以[路线图](docs/development-roadmap.md)为准；旧入口全文见[历史归档](docs/archive/2026-10-10-claude.md)，历史的“当前”及测试数字不代表最新状态。

## 产品与架构

- 中文聊天式采购审查助手，面向不同品类。描述需求或上传PDF/XLSX→动态指标候选→保存并明确确认→审查/追问/原文/复核/导出。旧固定八字段只保留历史回归。
- 动态清单最多20品目、100指标；numeric、money、enum、presence、duration、manual六类规则。未知标准待确认，不编造阈值；item_id与型号/SKU独立。
- 模块化单体：FastAPI与Celery共享业务代码，MySQL是状态权威，Redis是broker。Run+Outbox同事务，attempt/fence、权限和版本由服务端控制。
- `agent/assistant.py`是有预算的聊天/清单路由；`agent/dynamic.py`使用LangGraph做动态审查；`agent/graph.py`是旧八字段回归。模型选择受控工具、提出事实；来源守卫、覆盖检查、Decimal规则给正式结论。
- RAG：范围内BM25 + Ollama/BGE-M3（1024维）+ Milvus/RRF。SQL ready manifest、权限、快照、指纹/generation在联网前后重验；范围/索引/预算失败只在允许范围词法降级，说明原因。
- MySQL保存原对话、有限近期历史、版本化任务记忆和确认的个人偏好。旧回答/摘录不是证据；目录/原文/修改schema按需载入，时间按需读取，纯日期不调用模型，不逐轮注入时间。

深入：[架构](docs/architecture-and-interview.md)、[动态指标](docs/dynamic-checklists-b-2026-10-04.md)、[记忆](docs/context-memory-2026-10-04.md)、[上下文](docs/repair-assistant-context-audit-2026-10-09.md)、[范围检索](docs/document-item-binding-a3-2026-10-10.md)。

## 技术栈与目录

| 层 | 约定 |
| --- | --- |
| Python | Conda docucheck，Python3.12；backend锁文件为准，不用旧Python3.13环境替代 |
| 前端 | Vue3/TypeScript/Vite/Keycloak JS/PDF.js；frontend/package-lock.json锁定 |
| API/数据 | FastAPI/Pydantic2/MySQL8.4 InnoDB/SQLAlchemy2/PyMySQL/Alembic |
| 后台/模型 | Celery/Redis/LangGraph/LangChain接口/配置化OpenAI兼容Provider |
| 解析/检索 | MinerU PDF适配、openpyxl/defusedxml、BM25/Milvus/Ollama BGE-M3 |
| 验证 | pytest/Ruff/Vitest/TypeScript/浏览器/GitHub Actions |

代码：backend/app下agent、criteria、evidence、providers、db、api、vector_store；backend/tests、backend/migrations、frontend、evals、scripts。配置写忽略的.env，不输出/提交密钥。原件用相对storage_key，本机data在worker挂载/data。安装/启动见[复现](docs/reproduction.md)，端口以实际配置为准。

## 必须保留的业务与安全约束

1. 业务状态只有满足、不满足、资料冲突、证据不足、待人工确认；处理状态独立。不完整不能冒充证据不足，无定位证据不能判满足。
2. 完整审查全量覆盖适用资料；top-k无命中不证明字段缺失。范围来自确认的品目对应/冻结版本；首轮省略item_id仍是普通授权浏览，不能称自然语言硬隔离。
3. 模型不能设置Case、文件角色、供应商、权限、快照、范围白名单，不能访问任意SQL、路径、URL或shell。对象ID核对归属，OIDC/项目角色在服务端校验。
4. PDF保存页码/可用bbox，XLSX保存表/单元格；核对原件SHA-256、当前版本、限定语和口径。采购证据、会话记忆、执行状态分层。
5. 人工只修正有原文支持的提取事实；追加理由、来源、版本后重算。同一原文支持多个值保留冲突/人工复核，不直接修改历史结论。
6. 数值/金额用Decimal，API用十进制字符串，时间UTC ISO8601。核验供应商交期承诺；起点或自然日/工作日不同须确认。
7. 决策/工具/抽取/修复共享调用、token、deadline预算；记录行动摘要、工具事件、实际用量，不要求私有思维链，不承诺模型请求exactly-once或任意图节点精确续跑。
8. worker_lost进入interrupted；outcome_unknown明示，须用户显式retry，不自动重发可能已到达Provider的请求。
9. 文档是非可信数据，不执行指令、宏、公式、脚本。类型/大小/页数/解压量/路径预检；前端转义、XLSX导出防公式注入。密钥不进日志/报告/队列。
10. 复杂文档语义完整性不保证。解析/上下文/范围限额以实际schema/config为准，修改不绕过现有保护。

## 代码与API约定

- Python类型标注，Pydantic边界，repository/service数据库访问，规则独立可测；保留用户修改，避免无关重构。
- /api/v1；Case修改/提交问题带expected_revision；上传/Run/修正/重试/恢复带Idempotency-Key，同键异载荷409。
- projects/cases、供应商/文档、conversations/messages、agent-runs及events/results/facts/cancel/retry/resume/export、corrections、snapshot evidence/original。以OpenAPI、后端schema及前端api.ts为准，不增旧路径兼容层。
- 错误包含code/message/details/request_id；原件接口只收服务器ID，不接客户端路径。历史/导出用不可变Run快照。

## 协作和验证

- 当前用户授权优先于旧“小步暂停”记录；按已确认范围连续推进，每个大步骤测试/审查后独立提交到本地 Git；只有用户明确要求上传时才推送 GitHub，历史推送约定不再适用。
- 当前执行按[2026-10-13收尾计划](docs/superpowers/plans/2026-10-13-project-consolidation.md)：A固定范围/基线→B修关键缺陷→C当前效果评测→D有限整理与学习→E稳定面试演示；F受控试用另行验收。既有四步的前端与指定闭环已完成，B/C收束第3步，D/E收束第4步。当前只执行用户授权的A，不自动进入B。
- 新业务功能冻结；产品保持多品类采购，品目/规格/指标随任务配置，演示样例不构成品类白名单。允许bug修复、其他品类的资料/指标与反例评测、必要小范围整理及演示收尾。暂缓新增规则类型/复杂自动判定机制、多Agent、GraphRAG、网关自动路由、Redis缓存、换向量库、全量异步数据库迁移、Kubernetes/微服务。
- 已确认前端方案可继续；主要交互/样式变更先讨论。运行Docker/前端、调用真实模型、安装依赖前说明操作，离线检查不暗中执行。
- 遵守[当前协作协议](docs/step-by-step-plan.md)；历史按需读取，不反复加载全部记录。
- 全量：`python.exe -m scripts.verify_offline --report data/verification/<新文件名>.json`，网络守卫/报告不可覆盖。前端在frontend执行npm test和npm run build；安装沿用锁与既有约定。
- 新行为补回归，答案先由业务契约确定。离线/实连/真实模型分别报告，skip不算通过。独立质量、容量、费用和灾备不能以合成开发集代替。

业务下一步：A固定范围/基线已完成，接下来B先修通用数值语义与引用完整性，再鉴权阻塞、取消未知状态和上传对账。苹果/T恤两品目XLSX与手机资料只是可替换的演示样例，产品与评测保持多品类；旧八字段只承担明确历史用途。修复前基线标签audit-baseline-2026-10-13指向988962a，12份评测WIP有本地备份；未混入A文档提交。指定历史闭环见[品目对应C验收](docs/document-item-binding-c-acceptance-2026-10-11.md)，不代表稳定发布或独立质量。

动态评估D1协议/蓝图已交付；D2原件、评分、运行和检索有未提交WIP，状态按收尾计划核对。C先完成当前评测，检索20–30问与约20个Agent任务、六类核验分别记录；当前检索WIP仅6问/PDF-only。独立封存仍0，不把旧104/104或离线测试数量当当前准确率。每阶段验证后本地提交，不自动推送。
