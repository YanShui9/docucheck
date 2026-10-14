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
- 当前执行按[2026-10-13收尾计划](docs/superpowers/plans/2026-10-13-project-consolidation.md)：A固定范围/基线→B修关键缺陷→C当前效果评测→D有限整理与学习→E稳定面试演示；F受控试用另行验收。B指定门禁已通过：隔离MySQL 8.4.11/RR 13项及0018往返；最终运行后端41eb967/0018、四角色129份文件核对，六类规则11项已见合成核验、paper范围hybrid回读、原件与新旧JSON/Excel通过。保留两轮未完成及显式重试，含有效批次缓存；见[服务验收](docs/repair-consolidation-b-services-2026-10-13.md)。C开发测量已交付但质量出口未过，继续按真实失败修复，D/E收束第4步；不把指定门禁当稳定面试版、独立质量或生产发布。
- 新业务功能冻结；产品保持多品类采购，品目/规格/指标随任务配置，演示样例不构成品类白名单。允许bug修复、其他品类的资料/指标与反例评测、必要小范围整理及演示收尾。暂缓新增规则类型/复杂自动判定机制、多Agent、GraphRAG、网关自动路由、Redis缓存、换向量库、全量异步数据库迁移、Kubernetes/微服务。
- 已确认前端方案可继续；主要交互/样式变更先讨论。运行Docker/前端、调用真实模型、安装依赖前说明操作，离线检查不暗中执行。
- 遵守[当前协作协议](docs/step-by-step-plan.md)；历史按需读取，不反复加载全部记录。
- 全量：`python.exe -m scripts.verify_offline --report data/verification/<新文件名>.json`，网络守卫/报告不可覆盖。前端在frontend执行npm test和npm run build；安装沿用锁与既有约定。
- 新行为补回归，答案先由业务契约确定。离线/实连/真实模型分别报告，skip不算通过。独立质量、容量、费用和灾备不能以合成开发集代替。

业务下一步：C按实测反例修正式抽取、材料正文引用、枚举值和历史回读，再分析RRF排序与生产分块降级。12包/58原件/104位置，配置修正及显式恢复后6包completed、严格3/12、完整Run口径状态46/104、必要证据39/82；开发集作者已见，独立0。B原521份文件和2个waiting_user任务保留，98份无引用旧文件只审计备份。办公纸/保温杯、苹果/T恤和手机均为可替换样例；产品多品类，旧八字段只作历史回归。基线标签audit-baseline-2026-10-13指向988962a，原WIP与失败回执均保留。

动态评测最终工具179项定向、全量1829 passed/52 skipped/网络0，依赖/Ruff通过；该测量的业务服务为41eb967/0018，测量阶段未改生产业务或前端。检索24问（20有答案/4无答案）PDF-only/readback对照：BM25/dense/hybrid Recall@5为85%/92.5%/82.5%；不代表XLSX/MinerU生产入库或独立RAG质量。12正式任务和8附加场景分别报告，后者7/8。97次Agent供应商调用，318073输入/40169输出token、未知0、账单未知、剩31，当前停止模型发送。浏览器连接故障后以同开发账号PKCE HTTP继续，不称稳定浏览器验收；冻结买方context与供应商引用分通道计分，同raw观察重建不重发模型。详见[评估说明](docs/evaluation.md)及[机器摘要](evals/reports/dynamic-development-2026-10-14.json)。每阶段验证后本地提交，不自动推送。

C后续源码修复第一项：已有一次语义修复补受限候选级反馈，原始候选作为独立HumanMessage数据、UTF-8最多2048字节且明示省略；来源/覆盖/冲突/预算与旧规则/缓存身份不变。定向205项、当前源码全量1835 passed/52 skipped/网络0，依赖/Ruff及两阶段复审通过；此后续修复未部署或真实模型复测，不改写上述质量成绩，详见评估说明第9节。下一项已有重复枚举表头的离线反例，需保留旧规则/历史身份后修复。
