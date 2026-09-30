# DocuCheck 项目上下文

开始工作前，先阅读本文件、`README.md` 和 `docs/development-roadmap.md`，再检查 Git 状态与实际文件。设计目标、已实现功能和实测结果要分开描述；不要把本文当成代码已经完成的证明。

> **M2 阶段记录（2026-09-28）**：用户明确授权将 M2 剩余代码一次完成，替代下文“每次只做一个 M2 小步骤”的历史节奏。八字段全量抽取、规则、三供应商矩阵、Agent 上下文/档位、解析保守覆盖与离线评测已接通；当时后端 240 项测试及 `ruff check backend evals` 通过。**用户执行的真实 DeepSeek 单家和三家合成包探针均为 `completed`，八项与 24 项结果全部符合各自标注；三家轨迹显示逐家抽取、核验，引用未跨供应商。M2 合成开发包范围的功能验收通过。** 三家使用了服务端进度约束，最终提交有 `policy_override`；费用、完整时延及独立资料集质量尚未验收。见 [M2 验收记录](docs/m2-offline-acceptance-2026-09-27.md)及 [M2 任务拆分](docs/m2-task-breakdown-2026-09-25.md)。真实 API 调用、安装和 Docker 操作继续由用户在本机执行；开始任何前端代码前必须先暂停讨论设计规范与样式。下方时间线保留历史阶段事实。

> **M3 前审查修复（2026-09-28，覆盖上方旧测试数）**：比较词串字段、完整审查漏项、来源问答串供应商、宽 XLSX 行漏批与已知超预算审查、最终可见答案评测口径均已做离线修复。当前 `pytest backend/tests -q` **269 passed**，`ruff check backend evals` 和 `git diff --check` 通过；脚本化开发包任务 2/2、可见状态 32/32、引用错误 0/76，词法检索 17/17。本轮未调用 DeepSeek。容量预检是下界，不能保证所有解析上限内的文件都能一次完成；正式上传、独立资料集、多次真实模型试验、费用和完整时延尚未验收。详见 [M1/M2 审查及后续修复](docs/m1-m2-pre-m3-audit-2026-09-28.md)。可继续 M3 设计和实现，正式结果接口须通过上传/异步/幂等端到端验证；前端仍需先讨论设计。

> **M3 第一小步（2026-09-28，历史进度）**：已实现纯 Python Run 生命周期契约，覆盖领取、完成、取消、澄清恢复、中断重试及 attempt/fence/快照归属；当时后端 **275 项测试**、Ruff 通过。见 [M3 分步计划](docs/superpowers/plans/2026-09-28-m3-run-lifecycle.md)。跨进程安全仍需数据库原子条件更新；前端仍须先讨论规范与样式。

> **M3 第二小步（2026-09-28，迁移验收完成）**：SQLAlchemy Core 定义 12 张核心业务表，Alembic `0001_core` 首版迁移与 Windows Conda 依赖锁已加入；本机后端 **280 项测试**、Ruff、`pip check` 通过，SQLite 升降迁移及 MySQL 方言 SQL 生成通过。用户在本机启动 Docker MySQL 8.4，容器显示 `healthy`；`upgrade head` 无报错，`current` 显示 `0001_core (head)`。详见 [M3-2 验收记录](docs/m3-2-persistence-schema-2026-09-28.md)。repository 事务、API、worker 和前端尚未实现。

> **M3 第三小步（2026-09-28，验收完成）**：已加入 Run repository 的提交事务、新问题与 retry/resume 同键幂等、条件领取/完成/取消/重试/恢复及 Case 版本检查；离线后端 **293 passed, 1 skipped**，Ruff、`pip check` 通过。用户显式启用的真实 MySQL 并发测试另得 **1 passed**；详见 [M3-3 验收记录](docs/m3-3-run-repository-2026-09-28.md)。正式 API、worker 租约及前端尚未接线。

> **M3 第四小步（2026-09-28，范围内验收完成）**：新增本地 FastAPI Case/会话/消息/Run/事件入口及取消、重试接口；空 Case 没有上传快照时拒绝提交。离线后端 **296 passed, 2 skipped**，Ruff、`pip check` 通过；用户显式运行的 [M3-4 真实 MySQL API 测试](docs/m3-4-api-entry-2026-09-28.md)另得 **1 passed**。该测试在进程内调用 HTTP 路由，尚未验收网络服务启动；上传、Agent worker、认证和前端未接入。

> **M3 第五小步（2026-09-28，范围内验收完成）**：新增后端 PDF/XLSX 上传、文件角色/供应商绑定、随机路径存储、哈希/限额预检、不可变输入快照与同键重放；当前文档状态为 `pending`，尚无正式证据入库或 worker。离线后端 **300 passed, 3 skipped**，Ruff 与依赖检查通过；用户显式运行的 [M3-5 真实 MySQL 上传测试](docs/m3-5-upload-snapshot-2026-09-28.md)另得 **1 passed**。未开始前端。

> **M3 第六小步（2026-09-28，范围内验收完成）**：上传原件按 Case/快照解析前后复核路径、大小与 SHA-256；文档解析状态、覆盖信息和定位证据同事务提交；支持本机解析 CLI 和分页证据只读 API。离线后端 **306 passed, 4 skipped**，Ruff、依赖检查通过；用户显式运行的真实 MySQL 上传加 XLSX 解析测试另得 **1 passed in 2.28s**，并清除了测试环境变量。真实 MinerU PDF 尚未实测；Agent worker、认证、前端未接入。详见 [M3-6 记录](docs/m3-6-evidence-ingestion-2026-09-28.md)。

> **M3 第七小步（2026-09-28，单次来源问答验收完成）**：数据库快照证据原 ID 和来源可进入现有 LangGraph Agent；受控单次 Run 按 attempt/fence 提交答复、模型用量和 `outcome_unknown`。离线后端 **323 passed, 5 skipped**，Ruff 通过；用户显式运行的真实 MySQL 集成测试另得 **1 passed in 3.42s**。本机网络 API 上传并解析开发包报价后，真实 `deepseek-flash` 来源问答 Run 为 `completed`，5 次模型调用，正确定位 `报价!B6` 的 `16 GB`。首次真实答复显示文档 ID 而非文件名；已按测试先行修正后续答复的显示并离线验证，旧 Run 保留原答复。此验收仅覆盖单文件来源问答。Celery 派发、跨进程连续追问、认证和前端未接线。见 [M3-7 记录](docs/m3-7-persisted-agent-run-2026-09-28.md)。

> **M3 第八小步 A（2026-09-28，范围内验收完成）**：新增 Outbox 数据库租约领取、确认和失败延后；`delivery_attempts` 拒绝旧领取者写入，MySQL `SKIP LOCKED` 并发定向测试 **1 passed**。离线后端 **326 passed, 6 skipped**，Ruff 与依赖检查通过。本步不发布 Redis、不启动 Docker、不调用模型；Celery worker 接线、崩溃恢复和多轮持久化仍待后续。见 [M3-8a 记录](docs/m3-8a-outbox-claims-2026-09-28.md)。

> **M3 第八小步 B（2026-09-28，范围内验收完成）**：Outbox 发布器在数据库事务外将 `run.dispatch` 发至 Redis，Linux Celery worker 按 Run/attempt 领取并运行持久化 LangGraph Agent；重复投递受数据库 fence 保护。后端离线 **341 passed, 7 skipped**，真实 MySQL/Redis/Celery 空快照闭环 **1 passed**，worker 健康、自动派发已启动；本步未调用模型 API。强杀 worker 后 `running` 回收、Redis 丢消息对账、跨进程多轮持久化和前端仍待后续。见 [M3-8b 记录](docs/m3-8b-outbox-celery-2026-09-28.md)。

> **M3 第八小步 C（2026-09-29，范围内验收完成）**：Run 心跳、120 秒失联回收与 fence、结果未知的 API 可见性、已投递但未领取 Outbox 的延时重投已实现；旧 Case 版本不重复派发。Redis AOF 卷、MySQL 恢复索引与 `DATETIME(6)` 迁移已应用，本机 Alembic 为 `0003_recovery_precision`。离线后端 **350 passed, 8 skipped**，显式 MySQL/API/Redis/Celery 测试 **3 passed**，含独立进程强制终止且不调用模型的回收测试。跨进程多轮上下文、后台解析与前端仍待后续；见 [M3-8c 记录](docs/m3-8c-run-recovery-2026-09-29.md)。

> **M3 第九小步（2026-09-29，范围内验收完成）**：同会话同快照的最近四轮问答、已验证核验结果、焦点与待澄清问题可跨 worker 恢复；`POST /api/v1/agent-runs/{run_id}/resume` 将澄清回复入库并创建新 attempt。活跃 Run 互斥，恢复前验证来源、规则版本和资料指纹。本机 Alembic 为 `0004_conversation_memory`；离线 **357 passed, 9 skipped**，真实 MySQL 脚本化 Provider 定向 **5 passed**，未调用 DeepSeek。见 [M3-9 记录](docs/m3-9-conversation-recovery-2026-09-29.md)。后台解析与前端未开始。

> **M3 第十小步（2026-09-29）**：上传后的本机后台解析进程已实现当前快照文档的 MySQL 租约领取、XLSX/PDF 解析、迟到结果栅栏及三次失联转失败；Outbox 在文档解析结束前延后 Agent Run 派发。迁移为 `0005_background_parsing`，离线 **365 passed, 10 skipped**；本机启动方式与验收边界见 [M3-10 记录](docs/m3-10-background-parsing-2026-09-29.md)。真实 MinerU PDF 端到端解析与前端仍未验收；前端开始前先讨论规范和样式。

> **M3-11a（2026-09-29，修正基础）**：Run 记忆 v2 将经来源验证的事实集限额持久化，跨进程恢复时复核快照、供应商、证据和原文数值；v1 历史记录继续可读。离线 **379 passed, 10 skipped**，Ruff、依赖检查通过。见 [M3-11a 记录](docs/m3-11a-fact-persistence-2026-09-29.md)。修正提交、新版本规则重算和删除清理尚未实现；未调用模型或操作 Docker。

> **M3-11b（2026-09-29，来源事实修正）**：新增事实列表、修正提交和审计读取 API，修正值必须由原事实的同一证据原文支持；提交原子生成新快照、映射证据并让旧 Run 过期。新版本标为 `pending_recheck`，在确定性规则重算完成前拒绝新 Run。MySQL 迁移 `0006_corrections`，离线 **387 passed, 11 skipped**，真实 MySQL 无模型并发测试 **1 passed**；worker/dispatcher 已重建并恢复。见 [M3-11b 记录](docs/m3-11b-correction-submission-2026-09-29.md)。正式新结论及删除清理仍待后续；本步未调用 DeepSeek 或开始前端。

> **M3-11c（2026-09-29，确定性规则复核）**：新快照重验整组事实并保留同源旧值与新值，规则结果与 `needs_review` 分开保存；迁移到 `0007_correction_reviews`。离线 **396 passed, 11 skipped**，真实 MySQL 并发 **1 passed**。见 [M3-11c 记录](docs/m3-11c-correction-recheck-2026-09-29.md)。

> **M3-11d（2026-09-29，修正快照 Agent Run）**：复核记录在提交和执行前核验；Agent 工具绑定新快照的旧值、新值及规则结果，Run API 和答复呈现 `needs_review`。离线 **404 passed, 11 skipped**，Ruff 与依赖检查通过。本步未运行真实模型或 Docker 闭环，删除清理与前端待后续。见 [M3-11d 记录](docs/m3-11d-corrected-agent-run-2026-09-29.md)。

> **M3-12a（2026-09-29，Case 删除请求）**：新增 `DELETE /cases/{id}` 删除标记与同事务 Run/解析/Outbox 栅栏，拒绝后续写入并保留数据供受控清理。迁移 `0008_case_deletion_request` 已在本机 MySQL 应用；离线 **406 passed, 12 skipped**，MySQL 无模型竞争测试 **1 passed**。实际数据库及共享原件清理、长驻服务重启和前端尚未完成。见 [M3-12a 记录](docs/m3-12a-case-deletion-request-2026-09-29.md)。

> **M3-12b（2026-09-30，Case 受控清理）**：数据库租约与文件清单支持重试，跨 Case 共享原件有引用时保留，最终保留删除墓碑。MySQL 迁移 `0009_case_cleanup`，离线 **415 passed, 13 skipped**，合成 MySQL 定向 **1 passed**。长驻 cleaner 已启动，启动后待清理 Case 和清单均为 0；之后请求删除的资料会自动物理清理。前端仍未开始。见 [M3-12b 记录](docs/m3-12b-case-cleanup-2026-09-30.md)。

> **M3-12c（2026-09-30，长驻清理闭环）**：用进程内 API 和独立 Docker cleaner 验证合成 Case 上传、DELETE、状态完成及原件/关联数据清除，定向 **1 passed**，清理 1 个原件；测试后待清理 Case 和清单均为 0。网络 API、服务重启和真实大 Case 尚未验收；前端仍未开始。见 [M3-12c 记录](docs/m3-12c-cleaner-service-acceptance-2026-09-30.md)。

> **M3-12d（2026-09-30，网络清理闭环）**：临时 Uvicorn 网络 API 与独立 Docker cleaner 完成合成 Case 上传、DELETE 和原件删除，网络定向 **1 passed**；API 测试进程已关闭，待清理记录为 0。服务重启恢复和真实大 Case 未验收；前端仍未开始。见 [M3-12d 记录](docs/m3-12d-network-cleanup-acceptance-2026-09-30.md)。

> **M3-12e（2026-09-30，停机积压恢复）**：长驻 cleaner 正常停止期间，合成 Case 的 DELETE 保持待处理；重启原容器后完成文件和数据库清理，定向 **1 passed**，事后待删队列为 0。中途强杀与真实大 Case 尚未验收；前端仍未开始。见 [M3-12e 记录](docs/m3-12e-cleaner-restart-acceptance-2026-09-30.md)。

> **M3-12f（2026-09-30，文件删除后异常退出）**：独立 Python 清理子进程在持久清单形成、原件已删但清单未完成时异常退出；长驻 Docker cleaner 等 30 秒租约到期后重新领取，定向 **1 passed**，领取次数为 2，队列归零。未直接强杀 Docker 容器；前端仍未开始。见 [M3-12f 记录](docs/m3-12f-interrupted-cleanup-acceptance-2026-09-30.md)。

> **M3 Agent 部署核对（2026-09-30）**：发现旧 worker/dispatcher 镜像与 M3-11d 后的源码不一致，已使用与当前源码及依赖哈希一致的本地镜像重建；worker 健康，空快照自动派发测试 **1 passed**、模型调用 0 次。真实资料的修正 Run 尚未通过 Celery 闭环验收。见 [镜像同步记录](docs/m3-agent-deployment-sync-2026-09-30.md)。

> **M3 Agent 业务链路验收（2026-09-30）**：开发样例报价 XLSX 经 FastAPI 上传、后台解析、隔离 Redis/Celery worker 和容器内假模型完成来源问答，答复正确引用 `报价!B6` 的 `16 GB`，显式集成测试 **1 passed**。未调用 DeepSeek；测试 Case 已清理，正式 dispatcher 已恢复。修正快照及完整八字段链路尚待实测。见 [隔离闭环记录](docs/m3-agent-worker-e2e-2026-09-30.md)。

## 最新协作约定（2026-09-25，优先执行）

> 2026-09-29 更新：用户已明确授权 AI 代为执行本地繁琐操作；需要使用 Docker 时先告知。真实模型 API 调用仍先说明用途、发送范围和可能费用；开始前端设计或代码前先暂停讨论规范与样式。下段关于额外操作由用户执行是历史记录。

用户要求先确认计划、一次一个小步骤，不连续实施多个阶段。执行前阅读 `docs/step-by-step-plan.md`；先说明本步内容与验收，用户确认后只做本步。API 配置/调用、安装、启动容器等额外操作先给步骤，由用户执行。不要自动启动并行开发。M1 的七个小步骤已在模拟包与内存字段范围内完成；[M1 验收小结](docs/m1-acceptance-2026-09-25.md)区分真实模型结果、离线验证与未实现能力。真实两轮对话第三次尝试通过：首问 6 次模型调用返回“资料冲突”，追问 3 次调用复用同会话、同快照的核验结果并读原文；当时后端离线测试 90/90、全目录 Ruff 和 `pip check` 通过。当时追问附带需求来源的问题已在 M2-1 做离线修正；继续每次只实施一个经用户确认的小步骤。

2026-09-25 的 M1 后续审查见 [M1 审查记录](docs/m1-review-2026-09-25.md)：当时离线复现分列 XLSX 标签/数值漏抽后错误判“满足”，以及 A 的答复混入 B 的证据。它们不改变 M1 指定模拟包的有限验收，但进入真实表格与三家供应商前必须修正。[M2 任务拆分](docs/m2-task-breakdown-2026-09-25.md)按小步安排；当日只完成审查和拆分。

2026-09-26：M2-0 已实施并通过离线验收（当时后端 102 项测试、Ruff、依赖检查）；只支持明确的未合并相邻两格内存证据，模型漏抽保持不完整，合并 XLSX 标为 `partial`。详细边界见 [M2-0 小结](docs/m2-0-acceptance-2026-09-26.md)。

2026-09-26：M2-1 已完成离线修正（当前后端 110 项测试、Ruff、依赖检查）。正式内存核验答复只引用当前单个结果的来源，A/B 结果与证据不再混用；冲突来源追问聚焦报价与规格书。见 [M2-1 小结](docs/m2-1-acceptance-2026-09-26.md)。下一小步为 M2-2；完整 M2 和修正后的真实模型对话尚未验收，后续仍一次只做一个小步骤。

2026-09-26：M2-2 拆为小步；[M2-2a PDF 源文字覆盖校验](docs/m2-2a-pdf-source-coverage-2026-09-26.md)已完成离线修正（后端 113 项测试、Ruff、依赖检查）。正式 PDF 解析和模拟包缓存加载会对照原 PDF 的可提取文字；漏抽标为 `partial`，不发布正式内存结论。下一小步为 M2-2b XLSX 错误范围声明；整个 M2-2 尚未完成。

2026-09-26：[M2-2b XLSX 范围声明校验](docs/m2-2b-xlsx-dimension-2026-09-26.md)已完成离线修正（后端 117 项测试、Ruff、依赖检查）。实际单元格超出声明范围时恢复可读取证据，并标为 `partial`；藏在错误声明之外的超限行列会被拒绝。下一小步为 M2-2c PDF 非文字区域；整个 M2-2 尚未完成。

2026-09-26：[M2-2c PDF 嵌入图片覆盖检查](docs/m2-2c-pdf-embedded-images-2026-09-26.md)已完成离线修正（后端 118 项测试、Ruff、依赖检查）。正文可提取但同页图片被漏掉时，正式解析和缓存绑定均标为 `partial`。下一小步为 M2-2d 矢量绘制/表格结构；整个 M2-2 尚未完成。

2026-09-26：[M2-2d PDF 矢量绘制与注释覆盖检查](docs/m2-2d-pdf-vector-and-annotation-coverage-2026-09-26.md)已完成离线修正（后端 125 项测试）。正文之外实际绘制的线框、Form 内矢量以及注释会保守标为 `partial`；纯文字 Form 和未使用的绘图 Form 不受影响。无框表格的语义关系、XLSX 其他结构和任务级限额仍待处理；下一小步拟处理 XLSX 其他结构，M2-2 尚未验收。

2026-09-26：[M2-2e XLSX 图片与图表覆盖检查](docs/m2-2e-xlsx-drawing-coverage-2026-09-26.md)已完成离线修正（后端 127 项测试、Ruff、依赖检查）。工作表实际引用的绘图即使没有被单元格解析读到，也会记录工作表和关系 ID、标为 `partial`；单元格证据仍保留。下一小步拟处理 XLSX 批注与旧式绘图；隐藏行列、无框 PDF 表格语义及任务级限额仍待处理，M2-2 尚未验收。

2026-09-27：[M2-2f XLSX 常规批注与旧式绘图覆盖检查](docs/m2-2f-xlsx-comments-legacy-drawings-2026-09-27.md)已完成离线修正（后端 131 项测试、Ruff、依赖检查）。工作表的旧式绘图标签及实际批注关系均保守标为 `partial`，支持非默认工作表 part 路径。下一小步拟检查隐藏行列；线程批注、任务级限额和 PDF 无框表格语义仍待处理，M2-2 尚未验收。

2026-09-27：[M2-2g XLSX 隐藏行列覆盖检查](docs/m2-2g-xlsx-hidden-rows-columns-2026-09-27.md)已完成离线修正（后端 144 项测试、所改后端文件 Ruff、依赖检查）。隐藏行列、分组隐藏列及零尺寸行列会保留证据并标为 `partial`，负尺寸被拒绝。下一小步拟检查隐藏工作表；线程批注、任务级限额和 PDF 无框表格语义仍待处理，M2-2 尚未验收。

**前端暂停点。** 凡将要设计或修改前端页面、组件、样式或交互，先提醒用户并暂停前端实现。先与用户讨论前端设计规范、页面流程、视觉风格和样式，形成经用户确认的方案；只有用户明确同意开始写前端代码后才实施。一般性的“继续”不视作跳过此前端讨论。当前尚未开始前端实现。

## 产品范围

本项目是本地单用户的采购资料初审 Agent 工作台。一次任务包含 1 份笔记本采购需求，以及 1～3 家供应商各 1 份报价和 1 份规格书；首版只接收可提取文本的 PDF 和普通 XLSX。用户上传后提问，单 Agent 根据问题及工具结果动态决定检索、抽取、核验、继续取证或澄清。比较八项：内存、存储、屏幕尺寸、重量、质保、采购数量、单价、交付期限。产品型号用于关联资料，不计入八项。

当前唯一 Agent 架构基线为 `docs/superpowers/specs/2026-09-24-agent-first-design.md`，覆盖 Provider、状态图、工具、上下文、RAG、预算、恢复和评测；同日两份 v0.1 Agent 文档仅作历史。具体任务在 `docs/superpowers/plans/2026-09-24-agent-mvp.md`，执行节奏以 `docs/step-by-step-plan.md` 为准。Provider 的真实单次工具调用、三批模拟资料抽取与 Agent 连续两轮对话均已有用户反馈和本机记录；M1 的最终范围与限制见验收小结。

业务结论只有：满足、不满足、资料冲突、证据不足、待人工确认。文件处理状态（如 partial、failed、interrupted）与业务结论分开。解析失败或来源未覆盖时，不得把字段当成缺失；没有可定位证据时不得判“满足”。最终采购决定由人作出。

首版上限：单文件 10 MiB、PDF 全任务 30 页；XLSX 每文件最多 5 表、每表 1,000 行/30 列、总非空单元格 20,000、单元格 2,000 字符、解压后总量 50 MiB。质量目标是状态准确率 ≥85%、确定性结论的必要证据定位率 ≥95%、明确不达标却判满足为 0；这些均为待验收目标。

## 技术栈与运行方式

| 层 | 约定 |
| --- | --- |
| 本机 Python | Miniconda `docucheck` 环境，Python 3.12；不要使用项目旁的 uv `.venv`（目前为 Python 3.13）代替 |
| 前端 | Vue 3、TypeScript、Vite、Element Plus、Vue Router；Pinia 仅用于少量跨页 UI 状态 |
| API | FastAPI、Uvicorn、Pydantic 2 |
| 数据与迁移 | MySQL 8.4 / InnoDB、SQLAlchemy 2、PyMySQL、Alembic |
| 异步任务 | Celery 5、Redis broker；MySQL 保存权威业务状态 |
| 解析 | MinerU 4.0.7 为 PDF 开发验证候选；XLSX 使用 openpyxl、defusedxml |
| Agent 与模型 | LangGraph 1.x 单 Agent；LangChain 模型/工具接口 + 配置化 Provider；模型理解问题、选择受控工具并提出带证据引用的候选事实 |
| 判定与质量 | Python 规则引擎 + Decimal；pytest、Ruff、前端类型检查/Vitest、Playwright、GitHub Actions |

Windows 上运行 API 和 Vite；Docker Compose 运行 MySQL、Redis 和 Linux Celery worker。项目 MySQL 映射到 `127.0.0.1:3307`，Redis 映射到 `127.0.0.1:6379`。本机 `data/` 在 worker 内是 `/data`；数据库只存相对 `storage_key`。本地凭据写入被忽略的 `.env`，不得提交。启动与检查命令见 `README.md`。

## 代码与证据约定

- 后端按模块化单体组织，API 和 worker 共用业务代码。新增 Python 代码使用类型标注；请求和响应由 Pydantic schema 定义，数据库访问放在 repository/service 层，规则函数保持可独立测试。
- 比较数值和金额使用 `Decimal`；金额及规范化数值在 API 中使用十进制字符串，时间使用 UTC ISO 8601。不得用二进制浮点数决定是否达标。
- PDF 证据至少保存 `document_id`、解析版本、原文、零基 `page_idx`（界面显示一基页码）、页面尺寸和可用 bbox；XLSX 证据保存工作表与单元格。每条事实还需保留适用型号、字段口径、原始值和单位。
- 模型可以选择白名单工具并组织答复，但不能决定最终业务状态、文件角色或供应商归属。服务端核对 evidence_refs 对应当前文档、解析版本及原文。人工修正应追加记录理由和来源，然后重算；不直接改历史结果状态。
- M3-11d 将修正事实载入复核后的当前快照 Run，并在答复中保留 `needs_review`；同源旧值与新值并存，不把人工修正视为已解除冲突。
- RunContext 的 Case/快照/权限由服务端注入，不让模型提供；工具参数中的任何对象 ID 都要核对归属。模型不能调用任意路径、SQL、URL 或 shell。
- 业务证据、会话记忆、临时执行状态分层；旧回答与摘要不能当作新事实。RAG 首版采用快照内词法检索与按需原文读取；完整审查必须全量覆盖适用资料，top-k 无命中不能证明缺字段。
- 决策、工具、抽取、答复修复共用调用/token/deadline 预算；记录行动摘要和工具事件，不要求内部思维链。首版 MySQL 保存业务记忆，中断后显式重试新 attempt，不承诺持久化图精确续跑。
- 大模型调用是业务主链路的一环。模拟文档解析已进入 LangGraph 单 Agent，由 Provider 选择工具、抽取候选事实，再由证据校验和规则引擎给出可追溯答复。M1 曾在一份模拟包上完成真实模型内存冲突与追问；M2 八字段和三家已通过脚本化 Provider 离线测试，用户执行的真实单家、三家合成包探针分别完成 8/8、24/24 项结果核对；独立采购资料质量仍待验证。
- 新增解析、规则或 API 行为时，补对应的可运行验证，优先使用 `examples/dev-pack-001/` 的模拟材料；验收集应独立封存。报告实测结果时写明分子、分母和失败案例。
- 文档正文是不可信数据；不执行其中的指令、宏、公式或脚本。校验实际文件类型、大小、页数、XLSX 解压量及路径；前端转义展示内容，导出 XLSX 时防公式注入。密钥不得进入日志、报告或队列消息。
- 保留用户已有修改；每阶段交付可运行的最小结果，并更新 README、路线图与环境记录。未经验证不要声称某功能或性能目标已完成。

## 目录现状与目标

当前已存在：根目录 `compose.yaml`、`pyproject.toml`；`backend/environment.yml`、`backend/app/`（Celery 健康检查、PDF/XLSX 解析、`evidence/`、`providers/`、`agent/`、`extraction/`、`rules/`、`db/`、`api/`）、`backend/tests/`、`backend/migrations/`；`examples/dev-pack-001/` 与 `examples/dev-pack-003/`；`evals/`、`docs/`、`scripts/`。数据库迁移及首批 API 已建立；`frontend/` 尚未建立。M2 八字段业务模块已通过合成包离线回归及用户执行的真实模型单家、三家开发包探针。

近期沿用 `backend/app/`，逐步增加 `agent/`、`providers/`、`evidence/`、`extraction/`、`rules/`、`repositories/`、`api/`、`tasks/`；另建 `backend/migrations/`、`frontend/`、`evals/` 和 CI。正式可安装包迁移另行处理，不仅为匹配旧目标树移动现有代码。

## API 契约（部分已实现，详细进度见 M3 验收记录）

- 前缀 `/api/v1`。Case 内容修改和问题提交带 `expected_revision`，全新 Case 创建不要求；上传、创建运行、修正、重试和澄清恢复带 `Idempotency-Key`。同键异载荷返回 HTTP 409。
- 资源：`/cases`、供应商/文档、`/cases/{id}/conversations`、`/conversations/{id}/messages`、`/agent-runs/{id}` 及其 events/results/cancel/retry/resume/export、`/cases/{id}/corrections`、`/evidence/{id}`、`/documents/{id}/content`。详细请求与返回见新架构；旧 `/questions`、`/runs` 均未实现，不建立兼容层。
- 运行需支持取消与显式重试；健康接口为 `/health/live`、`/health/ready`。错误响应包含 `error.code`、`error.message`、`error.details`、`error.request_id`。文件读取接口只接受服务端生成的 ID，不接受客户端文件系统路径。
- 创建 Run 与 Outbox 在同一数据库事务内；worker 按 run/attempt 幂等处理，不承诺 exactly-once。导出读取不可变运行快照，和页面显示一致。
- Run 查询返回活跃 attempt 的 `outcome_unknown`。`worker_lost` 的 Run 为 `interrupted`，用户须显式 retry；系统不能自动重发可能已经到达模型服务的请求。

## 计划与当前进度

详细计划和各阶段验收条件以 `docs/development-roadmap.md` 为准；阶段 0 记录见 `docs/superpowers/plans/2026-09-24-phase-0-foundation.md`，环境实测见 `docs/environment/2026-09-24.md`。

1. **M0（已有基础资产）**：Conda、模拟样例、MinerU 验证、Docker 服务、解析代码；应用依赖和复杂内容覆盖尚未完成。
2. **M1（模拟包范围已验收）**：最小真实 Agent：版本化证据、Provider、词法检索/原文工具、内存抽取规则、LangGraph、CLI 两轮对话及事件。验收内存冲突与来源追问。
3. **M2**：八字段、完整覆盖抽取、三家供应商、上下文压缩、深入模式、澄清和质量评测。
4. **M3**：持久化会话 API、MySQL 迁移、Outbox/Linux 任务、幂等、取消和中断重试。
5. **M4**：Vue 上传对话工作台、来源、修正、历史、报告。
6. **M5**：独立验收集、真实模型质量/成本/性能报告、CI 与复现。

当前 PDF 校验覆盖 pypdf 可提取文字，并对嵌入图片、实际绘制的矢量内容、注释和疑似无框列布局保守标记未验证；`complete` 尚不能证明任意复杂 PDF 的语义关系均可恢复。XLSX 已检查合并单元格、错误范围声明、实际引用的绘图、常规及线程批注、旧式绘图、隐藏行列/工作表、页眉页脚和未知关系引用；任务级文件与页数限额已在入口执行。步骤 3 已选 `deepseek-flash`，凭据仅本地配置；真实 DeepSeek 的合成开发包探针已通过，但不能代替独立资料集质量及费用、时延验收。

首版质量和性能数字都是目标，尚无正式验收结果。原始需求与架构细节在用户提供的《DocuCheck-AI开发协作交接文档-v1.0.md》中；如本文与实时代码不一致，以实际代码和最新经确认的需求为准，并修正文档。
