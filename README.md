# DocuCheck

> **2026-10-08 Worker证书已应用（当前入口）**：[应用记录与启动命令](docs/repair-model-tls-applied-2026-10-08.md)确认worker只读CA/TLS校验生效，最小真实模型探针一次成功（304输入/35输出token），本轮138项定向/网络0、Ruff/pip check通过。Docker Hub超时后采用缓存运行环境更新应用，未证明标准镜像在线可重建；网页会话过期，苹果新报告仍待重新登录验证。下方待应用为上步历史，向量实连继续保留。

> **2026-10-08 模型连接定位（待应用）**：当前connection_error已复现为本机Kaspersky HTTPS检查根未受容器信任。已准备有限TLS错误分类和worker可选只读CA配置，保持证书/域名验证、禁止自动重试；证书位于共享data之外的忽略目录。应用前需用户确认信任该指纹匹配的本机根；尚未重建或恢复真实审查。见[根因、证据与回滚](docs/repair-model-tls-2026-10-08.md)。最终完整后端1266 passed/41 skipped/网络0，定向138项/网络0、Ruff/pip check和Compose隔离检查通过。

> **2026-10-08 登录后实连（部分完成）**：[产品路径记录](docs/repair-browser-product-2026-10-08.md)已验证动态清单保存/部分确认、上传解析/升版重确认、记忆有效期保存、历史合成报告/原文/下载，修复历史候选状态混入及产品标识文案。前端148项与构建、后端定向23项/网络0通过。真实模型候选1次成功，完整审查两次连接失败、用量未知；新资料闭环和BGE/Milvus仍待验证，下一步先查连接异常。本步独立提交本地Git。

> **实连第二部分A（2026-10-08）**：[本机端口与OIDC回调](docs/repair-local-login-2026-10-08.md)已修复，前端地址为 `http://127.0.0.1:15173/`，共用根.env端口、strictPort和既有客户端同步命令。最终后端定向14项、前端144项及构建通过；本轮全量1260 passed/41 skipped/网络0早于最后解析规则修正，最终修正另有定向回归。当前浏览器到达中文PKCE登录页，等待本机登录后继续业务页面验收；尚未调用模型或完成BGE/Milvus复验。

> **实连验收第一部分（2026-10-08）**：[本轮交付](docs/repair-live-database-2026-10-08.md)记录批次并发写入/漏读修复、有限数据库死锁重试、独立MySQL **29 passed**及当前后端 **1248 passed / 41 skipped / 网络0**。后台4服务各118个Python文件已同步，API健康200/未登录401；本轮未调用模型。前端5173被Windows保留，后续先处理端口/登录回调，再做浏览器、真实模型和向量检索闭环。下方R13全仓扫描对应此前源码基线；本轮补充的是3个变更Python文件的定向扫描。

> **R13 安全证据（2026-10-07）**：[当前扫描证据](docs/security/evidence/r13-final-2026-10-07.json)、[交付说明](docs/security/evidence/r13-delivery-2026-10-07.md) 和 [复现入口](ops/security/README.md) 提供工具/规则版本、源码清单/hash、实际覆盖、失败记录及具体处置。原 Mimosa 整文件豁免已移除，旧“0高危、15中危”口径已更正。扫描状态以证据中的 `complete/passed` 为准；实连和生产安全验收另行进行。下方第三组为本步之前的阶段记录。

> **当前修复状态（2026-10-07）**：[第三组历史、记忆与候选状态](docs/history-memory-fix-step-three-2026-10-07.md)已修复 R10/R11/R14，并关闭独立复查发现的失权迟到响应漏洞。最终后端 **1202 passed / 37 skipped / 网络0**、pip check/Ruff通过，前端 **134项 / 12文件**、类型检查及构建通过。第一组 `b5c67fe`、第二组 `8c314d3`，本组独立提交本地 Git。未启动或重启服务，R13 安全扫描证据和实连验收仍保留。

> **第一组修复记录（2026-10-07，阶段历史）**：[清单正确性第一组](docs/checklist-fix-step-one-2026-10-07.md)已完成保存确认、部分确认、版本隔离、升版证据匹配和手工六类指标配置修复。前端84项/构建、后端1191项离线回归及最新45项清单定向验证通过。本组纳入 Git 提交保存，本轮未重启运行服务或重复实连验收。[交接审查](docs/handover-review-2026-10-07.md)中的报告修正、历史/记忆及扫描证据问题仍待后续；较早完成记录不覆盖这些未关闭问题。

> **聊天前端第三大步（2026-10-07，当前，三大步完成）**：记忆偏好设置（三个有限键的查看/保存/删除/到期，真实 API 验证）、长历史渐进分页（后端 `total` + tail-first + 20 页预算）、Esc 关闭对话框、只读成员与 390px 小屏验收通过。前端 **65 tests passed**、构建通过，后端定向 26 项 + Ruff 通过。见 [第三步记录](docs/chat-ui-step-three-2026-10-07.md)。聊天式采购助手界面三大步全部完成；独立封存、真实多品类泛化准确率、远程 CI 与生产发布仍属后续验收条件。下方记录按交付日期理解为阶段历史。

> **聊天前端第二大步（2026-10-06，阶段历史）**：ChecklistPanel（候选查看/编辑、保存草稿→明确确认两步协议、部分确认、升版重确认）与 ReviewPanel（品目分组动态矩阵、持久化供应商中文名称、正式/非正式状态、事实修正审计、Excel/JSON 导出）已交付；文档读取响应新增持久化 `supplier_name`。前端 **57 tests passed**、构建通过，后端定向 57 项 + Ruff 通过；真实 OIDC + DeepSeek 苹果采购闭环（候选→保存→确认→上传→审查→报告→导出）通过，含升版重确认与 case_busy 边界实测。见 [第二步记录](docs/chat-ui-step-two-2026-10-06.md)。

> **聊天式采购助手后端收尾（2026-10-05，当前）**：普通中文聊天、无资料 revision 0、文字要求增删改候选、动态指标、上下文/记忆及真实 BGE-M3 + Milvus 混合检索已接入既有 Run/队列/API。候选须用户明确保存确认，正式审查按确认版本与完整覆盖执行；采购品类和指标名无白名单。最终离线 **1178 passed / 37 skipped / 网络0**，pip check/Ruff通过；独立实连 **2 passed**。真实队列四轮闭环完成，DeepSeek **7次请求**，索引/原文问答/正式审查/导出通过，测试资料已清理；含失败的收尾队列探针累计16次。实际迁移 **0016_assistant_runs (head)**，API/worker/indexer/cleaner已同步，宿主与关键容器源码hash一致。详见 [收尾及边界](docs/backend-completion-2026-10-05.md)、[接口与启动](docs/backend-assistant-api-2026-10-04.md)。Ollama当前使用本机11634；API/Ollama为前台进程，重启须按交接启动。**前端未改，下一步先讨论中文聊天界面规范与样式**。多品类离线回归不代表真实采购泛化准确率或生产高可用已达标。下方记录均按交付日期理解为阶段历史。

> **动态清单 B 后端主流程（2026-10-04，阶段历史）**：已支持从买方需求/订单提出指标候选，经用户明确确认后按动态清单核验；无需固定八项或限定采购品类。来源回答、版本绑定、批次重试、人工事实审计、JSON/Excel 导出已接入。见 [实现、API 顺序与限制](docs/dynamic-checklists-b-2026-10-04.md)。最终离线 **1123 passed / 35 skipped**、网络 **0**，真实 MySQL **11 passed**；head **0015_dynamic_agent**，合成 DeepSeek 探针 **8 次请求**。自然语言修改要求候选、普通聊天和动态语义检索路由仍待接入；页面及旧 API/Celery 未同步重启，前端仍须先讨论。B 的剩余项和 C 多品类验收见 [计划](docs/superpowers/plans/2026-10-04-dynamic-checklists.md)。下方记录为各模块交付时的历史。

> **动态清单 A（2026-10-04，阶段历史）**：每个用户、每次采购独立设置要求，不限制采购品类。已实现手工草稿、明确确认和不可变版本 API，包含权限、来源、修订冲突及删除约束。详见 [实现与接口](docs/dynamic-checklists-a-2026-10-04.md)、[A/B/C 计划](docs/superpowers/plans/2026-10-04-dynamic-checklists.md)。离线 **924 passed / 32 skipped**、网络 **0**，真实 MySQL **8 passed**；迁移 head **0014_dynamic_checklists**。**订单自动识别与通用动态核验属于下一步 B**，当前清单 execution_available=false，旧八字段运行器对动态 Case 明确拒绝执行。前端和旧 API/Celery 进程未同步，本轮真实模型调用 0；检索验收由另一位 AI 推进。下方是各模块的历史记录。

> **上下文与分层记忆后端（2026-10-04，最新）**：已实现近期八轮、带原消息/阶段来源的旧对话摘录、不可变任务 checkpoint、可确认/过期/删除的账号偏好、只读历史工具及上下文审计；连续澄清与失败重试均从实际记录恢复。见 [实现与使用边界](docs/context-memory-2026-10-04.md)。最终离线 **893 passed / 29 skipped**、网络尝试 **0**；真实 MySQL **3 passed**，迁移 **0013_layered_memory**。偏好已保存并注入，正式报告仍采用中文固定模板；网页/API/Celery 旧进程未重启，聊天路由及前端入口待接入。下一模块为用户自定义或需求/订单识别的动态审查清单；本轮未操作 Ollama/BGE 部署、调用真实模型或改前端。此为当前模块交付，不表示整个改造后端大步骤完成；下方为历史记录。

> **索引后端新增（2026-10-03）**：上传资料解析完成后会事务性登记 MySQL 索引任务；已实现异步 worker、失败重试、向量版本隔离、删除对账、状态/重试 API 和生产 Agent 检索接入。见 [运行说明与验收边界](docs/durable-indexing-2026-10-03.md)。最终离线 **874 passed/27 skipped**、网络请求 0；真实 MySQL 4 项、Milvus 合成向量 1 项通过。数据库已升级至 `0012_durable_indexes`。模型部署由用户另行安排，正式网页仍默认词法检索；旧 API/worker 进程未同步重启，真实 BGE 与容器连接需另做端到端验证。下一项继续上下文和记忆机制。

> **本机混合检索新增（2026-10-03）**：已实现可注入 Agent 的 Milvus+BM25Plus 检索组件，采用用户选定的 Ollama BGE-M3。安装步骤、无网络 preview 和本机语义对照命令见 [使用与验收记录](docs/hybrid-retrieval-2026-10-03.md)。正式网页自动索引尚未启用，真实 BGE-M3 语义效果待实测；聊天式前端、记忆和动态清单仍按改造计划推进。

> **Milvus 实连最新（2026-10-03）**：服务 2.6.25 + SDK 2.6.17 已通过真实合成向量和权限测试（3 passed）；root 口令已轮换，专用验收账号仅有 docucheck_probe 库的 13 项权限，默认 public 全局元数据读取已收紧。全后端离线 809 passed、25 skipped，网络尝试 0，依赖/静态检查通过。配置与密码保存在本机 .env；[实连报告及 Attu 说明](docs/milvus-live-acceptance-2026-10-03.md)。业务 RAG 与真实 Embedding 仍待接入，Attu GUI 连接仍待人工核对。下方状态为历史。

> **Milvus 启动更新（2026-10-03）**：新拉取的官方 2.6.25 已用独立卷启动，状态 healthy；健康接口 200 OK、未认证请求被拒绝。当前启动命令使用两份配置：`docker compose -f compose.milvus.yaml -f compose.milvus-smoke.yaml up -d --build`，受限为 2GiB、无自动重启。仍未安装 SDK 或接通业务 RAG；凭据轮换及真实向量检索验收待后续，见 [报告更新](docs/milvus-foundation-2026-10-03.md)。下方未启动描述为启动前记录。

> **当前改版进度（2026-10-03）**：用户已确认 Milvus 并授权开始后端改造。Milvus 配置、合成向量探针与独立部署文件已写好，定向 **81 passed**、Ruff/Compose 校验通过；默认预览不访问网络。资源不足，尚未启动新 Milvus 或接通业务 RAG。运行 `python.exe -m backend.app.milvus_probe` 可查看预览；实连、凭据轮换、SDK和 Attu 验证见 [本步报告](docs/milvus-foundation-2026-10-03.md)。保留四个大步骤，下一步推进后端；前端样式先讨论。下方为较早记录。

> **设计与调研修订（2026-10-03）**：[机制设计](docs/superpowers/specs/2026-10-03-context-memory-vector-criteria.md) 和 [Agent 架构调研](docs/superpowers/specs/2026-10-03-agent-architecture-research.md) 已更新：用户指定 Milvus，推荐 Standalone + 已有 Attu、单 Agent + 受控审查、分层记忆与动态清单。本机资源与 Milvus 连接尚未验证，本轮只完成调研/设计，没有实现这些扩展。

> **改版设计讨论（2026-10-03）**：已选定“聊天为主，审查面板按需展开”，正在确认中文流程、普通聊天及多品类动态审查设计。见 [现状与改版草案](docs/superpowers/specs/2026-10-03-conversation-first-procurement-redesign.md)。本轮仅审查及设计，当前运行版本仍使用设备八字段；草案中的新功能尚未实现。此前实测结果按各自版本理解。

> **最新源码联调（2026-10-03）**：第 1 项完成。独立 MySQL/Redis 栈与重建的 worker 已匹配当前后端源码；22 项服务测试全部通过、无跳过。真实开发质量 12/12 包、104/104 格、89/89 必要证据，危险满足 0、引用错误 0/244；固定五文件性能 10/10 达标、最高 62.125 秒。修复 Case 列表升版显示，前端最终 19 项、类型检查及构建通过；真实浏览器矩阵/导出、Bob 隔离、上传/修正/历史已核对。本轮上报 293 次模型调用，实际金额未知；独立人工封存仍 0。见 [本轮报告](docs/m5-current-version-integration-2026-10-03.md)。第 2–6 项仍待逐项执行；以下为历史记录。

> **交付后复查最新（2026-10-03）**：确认并修复 7 项代码问题，新增 41 项回归；原 Conda 和依赖锁对应的 Windows 隔离环境均 **765 passed、22 skipped**，网络尝试 0，pip check/Ruff 通过。包括历史规则结果读取/追问、八字段内存事实修正、混合版本正式标记、归档声明和离线边界。当前六个业务文件已修改，M5-B 真实模型成绩仍绑定原源码；本轮真实模型调用 0。新归档恢复 228 文件逐字节一致，资料错误 0；独立人工封存仍 0，生产/远程 CI 等条件保留。见 [复查报告](docs/m5-post-delivery-review-2026-10-03.md)。下方按日期保留历史结果。

> **M5-C 首次交付历史（2026-10-03）**：完整依赖锁、离线网络保护与 GitHub Actions、开发证据归档/恢复、启动和面试手册已交付。原 Conda、Windows 新环境、Linux 新环境各为 **724 passed、22 skipped**，网络尝试均为 0；前端 **18 passed**、类型检查/构建通过。最终 ZIP 的 227 项载荷已恢复并重验，12 包/104 格/38 原件无资产错误。业务源码与 M5-B 最终实测版相同，本轮新增真实模型调用为 0。独立人工封存仍为 0、实际账单未知；远程 CI、业务数据库灾备及完整生产发布尚未验收。见 [M5-C 交付报告](docs/m5-c-delivery-2026-10-03.md)、[复现手册](docs/reproduction.md)、[十分钟演示](docs/interview-demo.md)。下方较早阶段、测试数字和暂停点均按其日期理解。

> **M5-B历史（2026-10-03，进入 M5-C 前）**：最终fields-v4源码/worker同步，12包任务12/12、状态104/104、必要证据89/89、危险满足0、引用错误0/247；固定五文件性能9/10成功且≤120秒，第5次执行中断保留失败，跨两天恢复仅继续未开始任务。额外追问/取消/未知供应商/同源修正4项通过，修正139.44ms；最近完整后端690 passed、22 skipped，收尾检查通过。AI复核且作者已见，独立封存0、实际费用未知；该时点完成范围是M5-B开发验证，M5-C当时尚未开始。下方按日期保留历史结果和暂停点；它们不撤销用户已委托的AI复核与后续执行授权。见 [最终开发验证报告](docs/m5-b-development-validation-2026-10-03.md)。

> **M5-B历史（2026-10-02，早期开发版本）**：本机HTTPS信任、交期规则升级及历史修正兼容、Agent安全诊断、相邻内存标签/值和SSD检索均已修复。真实新两供应商校准16/16；固定v2五文件性能10/10成功且≤120秒。首轮12包仍为状态47/104、必要证据44/89，质量未达标；单包和性能成功不替换首轮分数。用户已确认采购承诺口径，当时代码与运行服务为fields-v3，完整回归626 passed、22 skipped。AI复核、作者已见，独立封存仍0；M5-B继续，未进入M5-C。详见 [最新交付记录](docs/m5-b-transport-and-calibration-2026-10-02.md)。v3全集及十次性能未复测；下方旧暂停点和测试数为历史。

本轮最终验证：后端 **626 passed、22 skipped**，定向规则/审计/会话56项、MySQL修正1项通过，Ruff、宿主与worker依赖及迁移检查通过。限定只读复审无可操作P0/P1/P2；跳过项不算通过，费用未知，正式独立验收未完成。

> **M5-A 最新（2026-10-02）**：版本绑定的独立人工复核与冻结校验工具已完成，见 [复核操作与本轮验收](docs/m5-a-review-gate-2026-10-02.md)。新增22项测试；后端 **518 passed, 22 skipped**，Ruff/依赖通过。实际复核目录是 `data/m5-review/`，104格仍待人核对，reviewed0/sealed0，未生成冻结记录；工具通过不能替代真实复核或业务质量测量。M5-A整体仍待人工条件，不进入M5-B；下方为历史记录。

> **M5-A 最新（2026-10-01）**：继续检查GitHub/社区材料后，按授权补齐12个新合成候选包、104格拟定标签及人工复核工作表。资料与标签定位工具新增21项测试通过，后端 **496 passed, 22 skipped**，Ruff/依赖通过。详见 [社区检索、候选包与复核步骤](docs/m5-a-candidate-corpus-2026-10-01.md)。**尚未独立复核或封存，未测业务质量/成本/真实性能**；留出候选不自动算未见集。本轮只做M5-A，不启动Docker/前端或调用模型；下方为历史记录。

> **M5-A 已开始（2026-10-01）**：按用户要求优先检索公开采购资料，已取得并核验7份原件，补充显式下载/离线哈希校验工具。新增测试16项通过，后端全量 **475 passed, 22 skipped**，Ruff与依赖检查通过。见 [M5三步计划](docs/m5-large-step-plan-2026-10-01.md)和 [来源调查及缺口](docs/m5-a-public-source-research-2026-10-01.md)。当前尚无独立标注或封存集，未调用真实模型，未进行业务质量/成本/性能验收；下方进度保留为历史记录。

> **M5 前审查（2026-10-01）**：已修复未知供应商替代、会话结果恢复校验、跨 Case 异步响应、追问会话选择及 multipart 实际请求体限额。后端全量 **459 passed, 22 skipped**、显式 MySQL **13 passed**、前端 **18 passed**，构建、Ruff、依赖与迁移检查通过；服务已同步当前源码。审查证据、复现命令和部署前剩余门槛见 [M0–M4 审查报告](docs/pre-m5-code-review-2026-10-01.md)。本轮未调用真实模型，M5 尚未开始。

> **当前状态（2026-10-01）**：M4-A/B/C/D 已在本地合成样例范围内验收，详见 [M4-C 记录](docs/m4-c-agent-workbench-2026-10-01.md)与 [M4-D 导出交付记录](docs/m4-d-export-delivery-2026-10-01.md)。真实 MySQL 已迁到 `0011_document_model_binding`；本地 OIDC 浏览器验证了单家 8 格、三家 24 格、追问、原件、修正、历史及 JSON/XLSX 导出。后端全量 `444 passed, 22 skipped`，前端 `12 passed`。本地脚本模型验收不等于真实 DeepSeek 质量或公网生产验收；下方较早的进度段落保留为历史记录。

> M2 阶段记录（2026-09-28）：M2 八字段采购审查的离线代码链路已接通；当时后端 240 项测试与 `ruff check backend evals` 通过。用户执行的真实 DeepSeek 单家和三家合成包探针均为 `completed`，分别与标注一致（8/8、24/24）；三家逐家抽取、核验的轨迹未发现跨供应商引用。M2 合成开发包范围的功能验收通过，费用、完整时延及独立资料集质量仍待验收。详情见 [M2 验收记录](docs/m2-offline-acceptance-2026-09-27.md)。下方按日期保留历史过程记录；前端开始前先讨论设计规范与样式。

> M3 前审查的可复现正确性问题已完成离线修复：比较词归属、完整答复覆盖、供应商来源隔离、宽 XLSX 行分批和已知超预算预检，以及最终可见答案评测。当前后端 **269 项测试**与 Ruff 通过；脚本化开发包 2/2 任务、32/32 可见状态、0/76 引用错误。容量预检只提供保守下界，真实模型修复后表现与正式上传链路尚未复验。详见 [M1/M2 审查及后续修复](docs/m1-m2-pre-m3-audit-2026-09-28.md)。

> M3 第一小步已实现 [Run 生命周期契约](docs/superpowers/plans/2026-09-28-m3-run-lifecycle.md)：领取、完成、取消、澄清恢复、重试均检查活跃 attempt、fence 与快照边界。后端 **275 项测试**和 Ruff 通过；MySQL 持久化、正式 API 与 worker 尚未接线。本轮未运行 Docker 或编写前端。

> M3 第二小步的 [MySQL 核心表和 Alembic 迁移](docs/m3-2-persistence-schema-2026-09-28.md)已通过离线检查（后端 **280 项测试**、Ruff、SQLite 升降迁移）。用户本机 Docker MySQL 8.4 容器显示 `healthy`，Alembic 升级无报错，`current` 为 `0001_core (head)`。当前只有数据表定义和迁移，事务 repository、正式 API 和 worker 尚未接线。

> M3 第三小步的 [Run repository](docs/m3-3-run-repository-2026-09-28.md)已完成验收：提交事务、新问题与 retry/resume 幂等、原子 Run 状态转换；离线后端 **293 passed, 1 skipped**，Ruff 通过。用户另行显式运行的真实 MySQL 并发测试 **1 passed**。正式 API、worker 和前端尚未接线。

> M3 第四小步的 [FastAPI 入口](docs/m3-4-api-entry-2026-09-28.md)已完成范围内验收：空 Case、会话、消息提交、Run/事件查询、取消和重试。离线后端 **296 passed, 2 skipped**，Ruff 通过；用户另行运行的真实 MySQL 进程内 API 测试 **1 passed**。文件上传、Agent worker、认证和前端尚未接入。

> M3 第五小步的 [后端上传与输入快照](docs/m3-5-upload-snapshot-2026-09-28.md)已完成范围内验收：PDF/XLSX 预检、角色与供应商绑定、文件哈希及随机路径、版本递增与快照保留。离线后端 **300 passed, 3 skipped**，Ruff 与依赖检查通过；用户运行的真实 MySQL 上传测试另得 **1 passed**。文档仍为 `pending`，解析入库、Agent worker、认证和前端尚未接入。

> M3 第六小步的 [上传资料解析与证据入库](docs/m3-6-evidence-ingestion-2026-09-28.md)已完成范围内验收：按快照解析、原件哈希复核、证据位置及覆盖信息入库、只读证据 API 与本机 CLI。离线后端 **306 passed, 4 skipped**，Ruff 和依赖检查通过；用户运行的真实 MySQL 上传加 XLSX 解析测试另得 **1 passed**。真实 MinerU PDF 尚未实测；Agent worker、认证和前端尚未接入。

> M3 第七小步的 [持久化证据驱动单次 Agent Run](docs/m3-7-persisted-agent-run-2026-09-28.md)已完成单文件来源问答范围内验收：正式上传快照的证据 ID 和来源进入 LangGraph Agent，Run 通过 attempt/fence 保存答复、用量和结果未知标志。后端 **323 passed, 5 skipped**、Ruff 通过；真实 MySQL 集成测试 **1 passed**。本机网络 API 上传及解析报价文件后，真实 `deepseek-flash` Run 为 `completed`，正确引用 `报价!B6` 的 `16 GB`。首次答复仅显示文档 ID 的问题已修正并离线验证，旧 Run 答复不变。Celery、连续追问恢复和前端尚未接入。

> M3 第八小步 A 的 [Outbox 领取与过期恢复](docs/m3-8a-outbox-claims-2026-09-28.md)已完成范围内验收：可领取待发 Run 事件、租约到期重领、拒绝旧领取者确认，MySQL 并发锁竞争定向测试 **1 passed**；后端离线 **326 passed, 6 skipped**。尚未发布 Redis 或接通 Celery worker；本步未运行 Docker、模型和前端。

> M3 第八小步 B 的 [Outbox 发布与 Celery Run 任务](docs/m3-8b-outbox-celery-2026-09-28.md)已通过本机闭环：后端离线 **341 passed, 7 skipped**，真实 MySQL/Redis/Linux Celery 空快照测试 **1 passed**、`model_calls=0`，worker 健康。自动派发已启动；新提交且资料就绪的 Run 将使用配置的模型。强杀 worker 后的 `running` 回收和 Redis 丢消息对账仍待 M3-8c；前端未开始。

> M3 第八小步 C 的 [Run 失联与队列消息恢复](docs/m3-8c-run-recovery-2026-09-29.md)已通过范围内验收：运行中 Run 每 10 秒写心跳、120 秒失联后标为 `interrupted / worker_lost / outcome_unknown`，旧结果被 fence 拒绝；已投递但仍排队的 Outbox 延时重投。离线后端 **350 passed, 8 skipped**，显式 MySQL/API/Redis/Celery 测试 **3 passed**，包括无模型调用的独立进程强制终止场景。Redis 已启用 AOF 卷，迁移到 `0003_recovery_precision`；跨进程多轮会话、后台解析和前端仍待后续。

> M3 第九小步的 [跨进程会话追问与澄清恢复](docs/m3-9-conversation-recovery-2026-09-29.md)已实现同会话同快照的最近四轮问答及核验结果持久化、澄清回复 API、连续澄清与活跃 Run 互斥。离线 **357 passed, 9 skipped**，真实 MySQL 定向 **5 passed**；迁移为 `0004_conversation_memory`。脚本化 Provider 测试没有调用 DeepSeek；后台解析与前端仍待后续。

> M3 第十小步的 [上传后台解析](docs/m3-10-background-parsing-2026-09-29.md)已实现 MySQL 租约领取、失联重领和旧结果栅栏；待解析文档会延后 Agent Run 派发。迁移为 `0005_background_parsing`，离线 **365 passed, 10 skipped**。PDF 的真实 MinerU 命令端到端处理仍待验收；前端尚未开始。

> M3-11a 的 [可复核事实集持久化](docs/m3-11a-fact-persistence-2026-09-29.md)为后续人工修正保存已验证的事实、扫描范围和证据引用；旧版 Run 记忆继续可读。离线 **379 passed, 10 skipped**。人工修正接口、新版本重算和删除清理仍待实现。

> M3-11b 的 [来源事实修正](docs/m3-11b-correction-submission-2026-09-29.md)已接通事实列表、修正提交和审计读取；新值须由同一原文支持，提交生成新 Case 版本并让旧 Run 过期。新版本处于 `pending_recheck`，规则重算前拒绝新 Run。离线 **387 passed, 11 skipped**，真实 MySQL 无模型并发测试 **1 passed**，迁移为 `0006_corrections`；worker/dispatcher 已更新。正式新结论、删除清理与前端仍待后续。

> M3-11c 的 [确定性修正规则复核](docs/m3-11c-correction-recheck-2026-09-29.md)已接通 `POST /api/v1/cases/{case_id}/corrections/{correction_id}/recheck`：新快照重验原文并重跑规则，保留同一原文支持的旧值和新值，标记 `needs_review`。离线 **396 passed, 11 skipped**，MySQL 并发 **1 passed**，迁移到 `0007_correction_reviews`，未调用模型。Agent 尚未消费修正事实，所以新 Run 仍受阻；删除清理与前端待后续。

> M3-11d 的 [修正快照 Agent Run](docs/m3-11d-corrected-agent-run-2026-09-29.md)已在后端接通：复核后可提交当前快照 Run，Agent 使用经再次核验的新旧双值事实，Run API 和答复保留 `needs_review`。离线 **404 passed, 11 skipped**，Ruff 与依赖检查通过；未运行真实模型或 Docker 闭环。删除清理及前端待后续，前端规范与样式仍须先讨论。

> M3-12a 的 [Case 删除请求与运行栅栏](docs/m3-12a-case-deletion-request-2026-09-29.md)已实现：`DELETE /cases/{id}` 标记待清理，终止活跃 Run 并撤销解析及待派发任务，后续写入返回 `case_deleting`。本机迁移到 `0008_case_deletion_request`，离线 **406 passed, 12 skipped**，MySQL 无模型竞争测试 **1 passed**。实际数据及共享原件清理、长驻服务重启和前端仍待后续。

> M3-12b 的 [Case 受控清理](docs/m3-12b-case-cleanup-2026-09-30.md)已实现数据库租约、持久文件清单、共享原件引用检查及删除状态查询；本机 MySQL 迁移到 `0009_case_cleanup`，离线 **415 passed, 13 skipped**，合成 MySQL 清理测试 **1 passed**。长驻 `cleaner` 已于 2026-09-30 启动，会物理清理今后请求删除的 Case；启动后待清理 Case 为 0。前端仍须先讨论设计规范与样式。

> M3-12c 的 [运行中 cleaner 删除闭环](docs/m3-12c-cleaner-service-acceptance-2026-09-30.md)已用独立 Docker 清理容器及进程内 API 验证：合成 Case 上传、DELETE、状态完成、原件和关联记录清除，定向测试 **1 passed**；清理日志显示删除 1 个原件，测试后待清理队列为 0。网络 API、重启恢复和真实大 Case 时延仍未验收。

> M3-12d 的 [网络 API 删除闭环](docs/m3-12d-network-cleanup-acceptance-2026-09-30.md)已用临时 Uvicorn 与长驻 Docker cleaner 验证同一合成 Case 流程，定向 **1 passed**；Uvicorn 测试进程已关闭，清理后无待删记录。服务重启恢复、真实大 Case 和 Agent 链路仍待验收。

> M3-12e 的 [停机积压删除恢复](docs/m3-12e-cleaner-restart-acceptance-2026-09-30.md)已验证：停止 `cleaner` 后请求删除合成 Case，超过轮询周期仍待处理；重新启动原容器后完成删除，定向 **1 passed**，文件清理 1 个、队列归零。清理中途强杀恢复和真实大 Case 时延仍待验收。

> M3-12f 的 [文件删除后异常退出恢复](docs/m3-12f-interrupted-cleanup-acceptance-2026-09-30.md)已验证：独立清理子进程在删除合成原件后异常退出，长驻 Docker cleaner 等待 30 秒租约到期并重新领取，定向 **1 passed**、队列归零。该测试未直接强杀 Docker 容器；真实大 Case 时延与 Agent 链路仍待验收。

> [M3 Agent 镜像同步与无模型派发验收](docs/m3-agent-deployment-sync-2026-09-30.md)发现并修正了运行镜像早于当前源码的问题：worker/dispatcher 已同步，空快照 MySQL/Redis/Celery 自动派发 **1 passed**、模型调用 0 次。真实资料修正 Run 的 Celery 闭环仍待验收。

> [M3 Agent 上传解析至 Celery 的隔离闭环](docs/m3-agent-worker-e2e-2026-09-30.md)已用样例报价 XLSX 完成上传、解析、异步派发和带来源的答复，显式测试 **1 passed**；模型请求只发送到临时容器内假接口。修正快照、完整八字段和真实 PDF 链路仍待验收。

> 最新协作状态（2026-09-25）：[M1 最小真实 Agent](docs/m1-acceptance-2026-09-25.md)在模拟包与内存字段范围内完成验收；真实模型连续两问通过，后端离线测试 90/90、全目录 Ruff 与依赖检查通过。正式上传、其他七字段和独立质量评测尚未完成。后续仍按[分步协作计划](docs/step-by-step-plan.md)一次做一个小步骤。任何前端工作开始前须先暂停并讨论设计规范与样式。

> 2026-09-26：[M2-0 XLSX 内存分列证据修正](docs/m2-0-acceptance-2026-09-26.md)和 [M2-1 答复引用范围修正](docs/m2-1-acceptance-2026-09-26.md)已通过离线回归（当前 110 项测试）。下一小步为 M2-2 解析覆盖；M2 完整采购审查尚未完成。

> 2026-09-26：[M2-2a PDF 源文字覆盖校验](docs/m2-2a-pdf-source-coverage-2026-09-26.md)已通过离线回归（当前 113 项测试），能发现原 PDF 可提取文字在 MinerU 结果中静默漏抽。下一小步为 M2-2b XLSX 范围声明校验；整个 M2-2 与完整采购审查仍未完成。

> 2026-09-26：[M2-2b XLSX 范围声明校验](docs/m2-2b-xlsx-dimension-2026-09-26.md)已通过离线回归（当前 117 项测试），能发现错误范围声明导致的单元格漏读。下一小步为 M2-2c PDF 非文字区域；整个 M2-2 仍未完成。

> 2026-09-26：[M2-2c PDF 嵌入图片覆盖检查](docs/m2-2c-pdf-embedded-images-2026-09-26.md)已通过离线回归（当前 118 项测试）。正文与图片混合的 PDF 不再仅凭正文被判为解析完整。下一小步为 M2-2d 矢量绘制与表格结构；整个 M2-2 仍未完成。

> 2026-09-26：[M2-2d PDF 矢量绘制与注释覆盖检查](docs/m2-2d-pdf-vector-and-annotation-coverage-2026-09-26.md)已通过离线回归（当前 125 项测试）。可见线框、实际调用的 Form 绘图及注释不会仅凭正文判为完整。下一小步拟处理 XLSX 其他结构；无框表格语义和任务级限额仍待解决，M2-2 尚未验收。

> 2026-09-26：[M2-2e XLSX 图片与图表覆盖检查](docs/m2-2e-xlsx-drawing-coverage-2026-09-26.md)已通过离线回归（当前 127 项测试）。工作表实际引用的绘图被忽略时会标为 `partial`，并保留已读单元格。下一小步拟处理批注及旧式绘图；整个 M2-2 仍未完成。

> 2026-09-27：[M2-2f XLSX 常规批注与旧式绘图覆盖检查](docs/m2-2f-xlsx-comments-legacy-drawings-2026-09-27.md)已通过离线回归（当前 131 项测试）。实际引用的批注和旧式绘图不再仅凭单元格判为完整。下一小步拟检查隐藏行列；M2-2 尚未验收。

> 2026-09-27：[M2-2g XLSX 隐藏行列覆盖检查](docs/m2-2g-xlsx-hidden-rows-columns-2026-09-27.md)已通过离线回归（当前 144 项测试）。隐藏与零尺寸行列不再仅凭已读单元格判为完整。下一小步拟检查隐藏工作表；M2-2 尚未验收。

DocuCheck 是一个正在开发的本地采购资料初审 Agent 工作台。用户上传资料后提问，单 Agent 调用受控工具并返回带来源的答复；八字段正式结论由规则引擎计算。现有资产包括单家与三家模拟文档、PDF/XLSX 解析、快照证据、词法检索、DeepSeek Provider、八字段抽取/规则、持久化 Run 与异步 worker，以及 Vue 审查工作台和同源 JSON/XLSX 导出。真实模型曾在 M1 内存问题、M2 合成包及 M3 单次上传问答中运行；M4 页面已用本地脚本 Provider 完成真实 MySQL/OIDC 浏览器范围内验收，尚未以页面流程测 DeepSeek。

AI 协作上下文见 [CLAUDE.md](CLAUDE.md)；Codex 入口为 [AGENTS.md](AGENTS.md)。当前设计基线是 [Agent 架构 v1.0](docs/superpowers/specs/2026-09-24-agent-first-design.md)，涵盖模型、状态、工具、上下文、RAG、恢复和评测；阶段条件见 [路线图](docs/development-roadmap.md)。

2026-09-24 已按 Agent 为中心重排计划。[M1 实施计划](docs/superpowers/plans/2026-09-24-agent-mvp.md)中的模拟包内存链路已完成验收；M2 离线链路与已执行的真实探针见 [验收记录](docs/m2-offline-acceptance-2026-09-27.md)。历史阶段 0/1 名称不代表新的里程碑进度。

## 当前范围

首版处理一份办公笔记本采购需求及最多三家供应商的报价和规格书，比较八个字段，并保留可以回到原件的证据。实施顺序见 [开发路线 v2.0](docs/development-roadmap.md)；历史基础环境设计见 [阶段 0 设计](docs/superpowers/specs/2026-09-24-phase-0-foundation-design.md)。

## 本地 Python 环境

在 Miniconda PowerShell 中运行：

```powershell
conda env create -f backend/environment.yml
conda activate docucheck
python --version
```

已在开发机核对 Python 3.12.14。`environment.yml` 固定 Python 主次版本和样例所需的 Python 包版本。

如果 PyCharm 自动创建了 `.venv`，请在项目解释器设置中选用已有的 `docucheck` Conda 环境。项目要求 Python 3.12，`pyproject.toml` 已与此对齐。

运行开发样例检查：

```powershell
python -m pytest backend/tests/test_dev_pack.py -q
```

环境文件用于 Conda 引导；M5-C 已增加完整的开发/Windows 与 worker 依赖锁，Windows 和 Linux 隔离安装与回归已通过。新机器的精确安装、字体和离线检查入口见[复现手册](docs/reproduction.md)。

现有解析代码位于 `backend/app/parsing.py`。在已有的 `docucheck` Conda 环境中，可调用独立 MinerU 环境的可执行文件生成模拟包证据索引：

```powershell
python -m backend.app.parse_dev_pack --mineru-executable "E:\developTools\miniconda3\envs\docucheck-mineru\Scripts\mineru-kit.exe" --output data/dev-pack-evidence.json
```

这是本机开发包解析命令；正式上传由 `backend.app.parse_background` 长驻处理，Linux worker 执行 Agent Run。输出在被 Git 忽略的 `data/` 中，每条 PDF 证据包含页码与 bbox，每条 XLSX 证据包含工作表与单元格。

## 数据与状态

[`examples/dev-pack-001/`](examples/dev-pack-001/README.md) 保存完全模拟的开发样例和八条标准答案。M5 已另建 12 包、104 格作者已见的合成开发资料，原件、AI 复核及真实解析/模型质量和性能见[M5-B 报告](docs/m5-b-development-validation-2026-10-03.md)。独立人工封存仍为 0；真实资料须由资料持有人授权后使用。

## 本地基础服务

Docker Desktop 使用 Linux Engine。在项目根目录创建 `.env`（可从 `.env.example` 复制），将 MySQL 示例口令替换为本地随机口令；OIDC 演示账号口令由下方脚本生成。Windows 若保留 `3307`，先在 `.env` 设置 `DOCUCHECK_MYSQL_PORT=13307`，再运行：

```powershell
python.exe -m backend.app.dev_oidc_realm
docker compose up -d mysql redis
docker compose --profile agent up -d --build worker dispatcher
docker compose --profile agent ps
```

在已激活的 `docucheck` Conda 环境中，迁移数据库并另开本机终端运行解析进程。它负责上传后的 PDF/XLSX 自动解析；关闭终端后进程停止，重启时会继续领取待处理文档。PDF 需指向本机已安装的 MinerU：

```powershell
python.exe -m alembic -c backend/alembic.ini upgrade head
python.exe -m backend.app.parse_background --mineru-executable 'E:\developTools\miniconda3\envs\docucheck-mineru\Scripts\mineru-kit.exe'
```

Compose 启动 MySQL 8.4、Redis 7.4；显式启用 `agent` profile 后启动 Linux Celery worker 和 Outbox dispatcher。dispatcher 会自动处理新提交的 Run，资料就绪时 worker 可调用 `.env` 中配置的模型服务。MySQL 仅映射到本机 `127.0.0.1:${DOCUCHECK_MYSQL_PORT:-3307}`（本机 Windows 保留 `3307`，故 `.env` 使用 `13307`），容器内仍为 `3306`；Redis 仅映射到 `127.0.0.1:6379`。项目文件共享目录是 `data/`，在 worker 内为 `/data`。MySQL 和 Redis 使用各自的 Docker 卷；Redis 开启 AOF，每秒刷盘。首次配置的详细验证见 [环境记录](docs/environment/2026-09-24.md)。

`cleaner` 已单独启动；通常用 `docker compose --profile agent up -d --build cleaner` 部署或更新。它会自动物理清理已通过 Case DELETE 接口标记的资料、历史记录及无其他 Case 引用的上传原件；操作不可恢复。可用 `docker compose --profile agent ps cleaner` 查看状态，或用 `python.exe -m backend.app.cleanup_cases --once --case-id <Case ID>` 对指定已标记 Case 执行一次清理。

本地 OIDC 演示使用 `alice`、`bob`、`reviewer` 三个账号。先在已激活的 Conda 环境中生成被忽略的 realm 文件和本地口令，再启动 Keycloak；登录页由 `docucheck-login` 自定义主题渲染（中文界面、产品视觉），主题文件在 `deploy/keycloak/themes/`；API 和前端分别在本机新终端运行：

```powershell
python.exe -m backend.app.dev_oidc_realm
docker compose --profile identity up -d keycloak
./scripts/start-api-oidc.ps1
# 在另一个终端的 frontend/ 目录：npm ci，然后 npm run dev
```

演示账号密码只在本地 `.env` 中查看，不要提交。当前默认页面地址为 `http://127.0.0.1:15173/`（根 `.env` 的 `DOCUCHECK_WEB_PORT` 可覆盖；已有Keycloak按[启动说明](docs/reproduction.md)同步回调）。启用 worker 后，页面新提交的问答会调用 `.env` 配置的模型服务并可能计费；M4 验收使用隔离的本地脚本 Provider，没有发送 DeepSeek 请求。工作台选定有结构化核验格子的 Run 后，可点击“导出 JSON / 导出 XLSX”；历史或部分结果保留非正式标记。下载接口为 `GET /api/v1/agent-runs/{run_id}/export?format=json|xlsx`，需相同 Case 读取权限。完整无模型验收命令和演示顺序见 [M4-D 记录](docs/m4-d-export-delivery-2026-10-01.md)。

仅停止 Run 自动处理可运行 `docker compose --profile agent stop dispatcher worker`；停止自动删除可运行 `docker compose --profile agent stop cleaner`。停止全部服务可运行 `docker compose --profile agent stop`，这些命令都保留数据库卷。

当前已有 Case/会话/Run、上传、原件与证据读取、结构化 CheckResult API、JSON/XLSX 导出和数据库迁移；本机解析进程运行时会自动处理上传文档，手动解析 CLI 可用于排障。Outbox、Redis 发布、Celery Run 自动执行、失联回收和跨进程多轮追问已接通；失联后的模型结果标为未知，只能显式重试。Vue 页面可进行上传、对话、八字段矩阵、证据复核和导出；本地 Docker/OIDC 合成样例验收见 [M4-C 记录](docs/m4-c-agent-workbench-2026-10-01.md)和 [M4-D 记录](docs/m4-d-export-delivery-2026-10-01.md)。
