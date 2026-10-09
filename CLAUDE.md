# DocuCheck 项目上下文

> **2026-10-09 分阶段上下文修复**：[设计与验证](docs/repair-assistant-context-stages-2026-10-09.md)。真实十项候选追加交期因必需输入16014>16000而在模型前失败；路由改为轻量准备/目录工具，编辑时按目标加载完整patch schema，完整基准留服务端，未改字段原样保留。目录按预算缩页、品目独立分页、准备目标越界拒绝、复杂单项明确提示，加入北京时间。真实失败只读脚本回放两阶段14039/15883，模型API与业务写入0；应用状态以配套报告为准。

> **2026-10-09 连续候选修复已应用（当前）**：[应用记录](docs/repair-assistant-preview-applied-2026-10-09.md)已应用`5a5412d`，同对话未保存候选承接、数量/ID/来源保留，仍须明确保存确认；MySQL旧读视图与Case优先加锁回归关闭。完整1304/41/网络0，前端172/类型构建、MySQL1通过。四后台480源码匹配、依赖配置不变，OIDC API/页面200、匿名401；两个waiting_user/五条ready/原七Run及真实清单保持，其他14容器未重启。真实模型与首次渲染历史现象未新增验收；旧程序不兼容新绑定封套，回退需遵循报告边界。

> **2026-10-09 连续候选修复（源码通过，待应用）**：[候选承接设计与验证](docs/repair-assistant-preview-2026-10-09.md)修复同对话连续补充遗漏“20台”，固定前置候选并重放校验，与保存/确认权限分离；跨对话、快照及清单版本隔离，16层须先保存。MySQL旧读视图与Case优先加锁回归通过。运行中服务仍为上步上下文修复版本，应用状态以本报告后续记录为准；真实已确认手机清单未改写。

> **2026-10-09 上下文修复已应用（当前）**：[服务恢复与应用记录](docs/repair-chat-context-applied-2026-10-09.md)已用工具管理终端恢复OIDC API8000/前端15173，直连200、匿名业务401。四后台应用`6601a71`镜像，4×119源码与仓库一致，依赖/配置保持，worker/indexer healthy；其他14容器、两个waiting_user、五条ready索引及解析/派发状态保留。实际新worker只读回放七项原失败上下文，通过15980/16000预算，未调用模型、未改原Run。下方已构建未部署为上步历史；未保存候选承接/20台遗漏、revision0卡片时序和真实模型行为仍待下一步。开发进程不是开机自启。

> **2026-10-09 手机采购对话排障第一步（当前）**：[上下文修复与边界](docs/repair-chat-context-2026-10-09.md)已修复七项manual基准下短聊天在请求前context_budget：精简schema展示注解、可选记忆显式让出预算、完整固定规则/问题保留及首轮数字审计；补采购准备指引。应用提交`6601a71`，完整离线1286 passed/41 skipped/网络0、Ruff/pip及独立复查通过，真实模型0。缓存镜像已构建并逐角色119源码核对，但未部署，latest已恢复原运行镜像。API/前端停止，后台启动被自动审批blocked by policy拒绝，等待用户本机启动；18容器、两个waiting_user及五条ready索引保留。下一步是应用镜像、未保存候选承接与“20台”遗漏、revision0卡片时序；原已确认七项未改。下方为此前交付历史。

> **2026-10-08 实连第二部分K（当前入口）**：[浏览器RAG与历史核验修复](docs/repair-browser-rag-live-2026-10-08.md)完成统一部署后的两轮真实页面/HTTP/队列/MySQL/BGE/Milvus/DeepSeek原文查询及上下文追问，均hybrid无降级；4次模型调用、15044输入/607输出token，无未知用量，费用及SDK请求数未独立计量。任务记忆v1/v2、刷新后选择任务恢复答复、原件hash及既有动态报告通过。修复历史入口误显示八字段、未验证raw答复、失权缓存/迟到详情及旧导出边界；最终前端170项/类型构建、后端定向32项/网络0、Ruff/pip通过，独立代码复核问题已关闭。未新增完整审查或重启Docker，waiting_user/五条ready索引保留；导航选中恢复和生产质量门槛仍保留。下方J为后台同步历史。

> **2026-10-08 实连第二部分J（历史记录）**：[后台版本统一](docs/repair-runtime-sync-2026-10-08.md)已更新worker/indexer/dispatcher/cleaner，四容器各118源码、合计472哈希与仓库一致；原差异分别1/6/6/6，现全部清零。各自缓存环境离线应用构建，依赖包版本未变、回退标签保留；首次错误FROM触发远程解析失败如实记录。定向167项/网络0、Ruff/pip及新worker只读修正报告检查通过。原waiting_user、五条ready索引、文档解析及派发状态保留；worker只读CA和其余14容器状态一致。主动模型/Embedding请求0，未新增网页/队列审查验收或做干净基础镜像重建。下方I为索引恢复历史。

> **2026-10-08 实连第二部分I（历史记录）**：[索引恢复与MySQL持久检索](docs/repair-index-recovery-2026-10-08.md)核对当前版本及项目成员后，显式重试五条failed索引，既有Docker indexer全部一次成功，11分块ready且原件hash一致。三个新诊断Run通过真实MySQL/BGE/Milvus混合检索、原文作用域、历史generation清理与终态访问拒绝；查询Embedding6次、DeepSeek0次，后台索引请求未独立计量。相关离线55项/网络0、MySQL并发1项、Ruff/pip通过。本轮是运维诊断，未做浏览器聊天/模型回答；既有waiting_user保留。worker仍有1文件差异，下一步统一部署。下方H保留恢复前索引状态及上轮测试范围。

> **2026-10-08 实连第二部分H（历史记录）**：[Milvus启动修复](docs/repair-milvus-startup-2026-10-08.md)用同版本独立etcd与健康门禁恢复原卷，补齐新卷目录初始化；实连发现public默认跨库元数据权限在重启时恢复，已关闭自动授权并撤销三项默认权限，重启隔离验证通过。最终真实Milvus/BGE5项通过（持久化SQL层为SQLite），此前1失败/4通过保留；本步Embedding6次、DeepSeek0次。完整离线1274/41/网络0，最后新增部署用例另由最终22项覆盖，Ruff/pip/Compose通过。原集合清单与waiting_user保留，5条failed索引未重试；worker1文件差异待统一部署。下方G为恢复前失败历史。

> **2026-10-08 实连第二部分G（部分完成）**：[BGE/Milvus复验](docs/repair-rag-reverification-2026-10-08.md)已恢复本机Ollama/BGE，1024维归一化及Docker indexer到宿主连通通过；既有Milvus两次启动均因启动期etcd leader changed退出134，未改数据卷或凭据。真实连接失败时两条合成查询显式BM25降级，不能算向量成功；本诊断Embedding3次、DeepSeek0次。补齐ready manifest依赖故障/释放/原文守卫两项，相关92项/网络0、Ruff/pip check通过。Milvus成功路径实连未运行、整项不勾选，下一项先修启动再验收持久化检索；worker1文件差异仍保留。下方F为已完成的正向修正历史。

> **2026-10-08 实连第二部分F（当前入口）**：[本步交付](docs/repair-correction-positive-live-2026-10-08.md)完成同源12/8箱备选的真实OIDC页面提交、MySQL冲突/非正式/审计及Excel/JSON/原件一致性。夹具报告用测试Provider准备，真实模型0次；修复“待复核误称抽取未完成”，完整1269 passed/41 skipped/网络0、定向31项及Ruff/pip check通过。API已通过OIDC脚本应用修复，记录首次漏带认证参数的重启误操作；worker未重建，1文件差异明确保留。产品页面链路可勾选，BGE/Milvus仍待实连。下方正向修正待验收为阶段历史。

> **2026-10-08 实连第二部分E（当前入口）**：[本步验收](docs/repair-correction-logout-live-2026-10-08.md)完成真实浏览器26箱无来源修正拒绝、重读恢复、主动退出/后退/重访检查，MySQL四次状态一致，三个未认证报告接口401。后端定向23项/网络0、前端148项/类型与构建通过；没有应用代码改动或模型请求。正向修正仅有自动化证据，仍需独立双值原文的浏览器提交验收；BGE/Milvus复验保留。下方“实际退出待验收”为阶段历史。

> **实连第二部分D（2026-10-08，当前入口）**：[苹果新资料闭环](docs/repair-apple-review-live-2026-10-08.md)已通过真实OIDC/HTTP/队列/DeepSeek完整审查、原件定位及Excel/JSON/数据库一致性核对，两项正式满足、批次1/1。修复表格事实抽取缺少指标上下文/原文单位的提示契约，批次增加有限拒绝原因；独立复核的型号绑定遗漏已关闭。最终后端1268 passed/41 skipped/网络0、定向155项、Ruff和镜像检查通过。当前worker118源码一致、只读CA保留。本步模型7次、22744输入/1066输出token，无未知用量；最终型号说明补充后未再调用模型。BGE/Milvus、新报告人工修正提交及退出验收仍待后续。下方等待登录为阶段历史。

> **实连第二部分C应用（2026-10-08，当前入口）**：用户已授权应用核对后的本机CA。worker只读信任配置生效，healthy、118个Python源码哈希一致，其余6容器未重启。实际SDK TLS验证及一次合成模型工具探针成功（304输入/35输出token）；本轮138项定向/网络0、Ruff和容器pip check通过。标准在线构建受Docker Hub连接阻止，使用已验证缓存运行环境更新应用；网页重试时OIDC会话过期，未新增业务请求，苹果完整审查仍partial，等待重新登录。见[应用、证据及启动/回滚](docs/repair-model-tls-applied-2026-10-08.md)。下方待应用是上步历史，BGE/Milvus仍未复验。

> **实连第二部分C（2026-10-08，待应用信任配置）**：[TLS根因与修复](docs/repair-model-tls-2026-10-08.md)已复现本机Kaspersky HTTPS检查根未受容器信任，指纹精确匹配Windows已信任根。新增安全TLS错误分类/Assistant事件、只作用于worker的可选CA只读overlay；独立复核发现共享data可写别名后已将bundle移到独立忽略目录.local-trust。最终完整后端1266 passed/41 skipped/网络0，定向138项/网络0、Ruff/pip check及Compose隔离校验通过。用户证书信任确认仍待回复，尚未应用配置、重建worker或完成新资料审查；本步诊断请求1次失败，用量未知。

> **实连第二部分B（2026-10-08，当前入口，部分完成）**：用户真实登录后，动态候选、手工/部分确认、上传解析与升版重确认、记忆有效期保存及历史报告/原件/导出已实测。修复历史候选串用当前状态/问题/保留徽章及通用产品标识文案；最终前端148项/构建、后端定向23项/网络0通过，独立复核通过。真实DeepSeek候选1次成功、完整审查两次connection_error且用量未知，共3次请求；新资料完整审查与BGE/Milvus仍未验收，下一步先定位连接异常。见[本步范围及证据](docs/repair-browser-product-2026-10-08.md)。

> **实连第二部分A（2026-10-08，当前入口）**：[端口/OIDC修复](docs/repair-local-login-2026-10-08.md)已将本机前端默认改为15173，统一根.env端口与既有客户端登录/退出回调，strictPort防止漂移；真实中文PKCE登录页及回调拒绝边界通过。最终后端定向14项、前端144项及类型/构建通过；本轮全量1260 passed/41 skipped/网络0启动于最后解析规则修正前，最终修正另由定向覆盖。等待用户在本机登录，登录后的页面/模型/BGE-Milvus完整链路仍待后续。每步独立提交；本轮模型调用0。

> **实连验收第一部分（2026-10-08，当前入口）**：真实 MySQL 发现并修复抽取批次旧 RR 读视图导致的重复写入/漏读，并补齐跨 Case gap lock 死锁的有限事务重试。最终独立临时库 **29 passed**、清理成功；完整后端 **1248 passed / 41 skipped / 网络0**，pip check/Ruff通过。4个后台容器各118个Python源码与宿主一致；API健康200、未登录401，原waiting_user任务保留。见 [本轮交付与剩余验收](docs/repair-live-database-2026-10-08.md)。本轮真实模型调用0；前端5173落入Windows保留端口，OIDC/浏览器/模型及BGE/Milvus闭环仍待下一部分。R13全仓扫描是修复前基线，本轮仅对3个变更Python文件补做扫描，不扩大历史证据结论。

> **R13 安全证据（2026-10-07，当前入口）**：新增当前源码隔离扫描、固定版本规则重建、逐文件覆盖/错误门禁和按源码 hash 的发现处置，移除原 Mimosa 整文件豁免。最终状态以 [扫描证据](docs/security/evidence/r13-final-2026-10-07.json) 和 [交付说明](docs/security/evidence/r13-delivery-2026-10-07.md) 为准；复现步骤见 [安全工具说明](ops/security/README.md)。不能沿用历史“0高危、15中危”作为当前结论，也不能将静态扫描通过解释为生产安全认证。下方第三组为本步之前的阶段记录；MySQL/OIDC/浏览器和真实模型实连仍待另一步验收。

> **交接修复第三组（2026-10-07，当前）**：[历史、记忆与候选状态](docs/history-memory-fix-step-three-2026-10-07.md)完成 R10/R11/R14：更早历史答复分批恢复、记忆有效期独立保存与原到期时间保持、权威清单状态同步及历史候选原 snapshot 只读；独立复查发现的失权迟到响应恢复私密内容已修复并补回归。最终后端 **1202 passed / 37 skipped / 网络0**、pip check/Ruff通过，前端 **134项 / 12文件**、类型检查及构建通过。本组按用户约定单独提交本地 Git；第一组 `b5c67fe`，第二组 `8c314d3`。剩余 R13 安全扫描证据和实连端到端验收，未启动或重启服务，未调用真实模型。

> **用户开发约定**：每个约定步骤完成后，先测试、复查和更新文档，再创建独立本地 Git 提交，报告提交号，确保可回滚。仓库未配置远程时不得把本地提交称为已经推送 GitHub。

> **交接审查后的第一组修复（2026-10-07，阶段历史）**：[清单正确性修复](docs/checklist-fix-step-one-2026-10-07.md)已完成 R01/R02/R03/R05/R06/R07：未保存编辑禁止确认、部分确认保留完整清单、异步响应作用域隔离、升版证据严格重绑定预览、空清单手动创建和六类规则切换。前端 84 项及构建通过，后端完整离线 1191 passed / 37 skipped / 网络0，清单/API最新定向45项通过；独立复查无新增阻断问题。本轮未重启运行服务、实连MySQL或调用模型，本组纳入 Git 提交保存。[交接审查](docs/handover-review-2026-10-07.md)中的报告修正/审计、历史/记忆和安全扫描证据问题仍待后两组处理；下方“三大步完成”是此前功能交付记录，不代表这些验收问题均已关闭。

> **聊天前端第三大步（2026-10-07，当前，三大步完成）**：记忆偏好设置（账号菜单入口，三个有限键的查看/明确保存/删除/到期说明，真实 API 验证）、长历史渐进分页（消息响应新增 `total`；首载最近一页 + "加载更早消息"渐进入口 + 20 页预算明确报错 + 发送后区间续读）、确认/偏好对话框 Esc 关闭、只读成员（bob viewer）与 390px 小屏浏览器验收全部通过。前端 **65 tests passed**、构建通过，后端定向 26 项 + Ruff 通过；静态注入检查干净。见 [第三步记录](docs/chat-ui-step-three-2026-10-07.md)。聊天界面三大步全部完成；独立封存、真实多品类泛化准确率、远程 CI 与生产发布仍属后续验收条件。下方记录按交付日期理解为阶段历史。

> **聊天前端第二大步（2026-10-06，当前）**：已交付 ChecklistPanel（候选查看/编辑、六类规则表单、保存草稿→明确确认两步协议、部分确认、升版"以上一版确认为底稿"）与 ReviewPanel（品目分组动态矩阵、持久化供应商中文名称、六类状态正式/非正式标记、批次进度、事实修正审计、Excel/JSON 导出联动）；文档读取响应新增持久化 `supplier_name`。前端 **57 tests passed**、vue-tsc/Vite 构建通过，后端定向 57 项 + Ruff 通过。真实 OIDC + DeepSeek 苹果采购闭环通过：候选→保存→确认→上传→审查（数量满足/交期证据不足，引用 报价!A1）→报告→导出，含升版重确认与 case_busy 边界实测。见 [第二步记录](docs/chat-ui-step-two-2026-10-06.md)。记忆偏好设置、长历史分页、面板焦点/键盘与统一验收为第三大步。下方记录按交付日期理解为阶段历史。

> **聊天前端第一大步（2026-10-05，阶段历史）**：已按用户定稿实现 Vue 中文聊天主页、可折叠采购任务导航、上传入口与资料/历史按需面板；接入真实 conversation/messages、assistant Run、空资料 revision 0、历史恢复、停止/澄清/显式重试和幂等请求。最终前端 **36 tests passed**，类型/生产构建通过，真实 OIDC + 苹果采购聊天 completed，桌面及390px小屏检查通过。产品标识选填，普通采购资料无需设备型号。动态清单编辑/保存确认及报告面板为第二大步，记忆设置和统一交互验收为第三大步。见 [第一步记录](docs/chat-ui-step-one-2026-10-05.md)、[三大步计划](docs/superpowers/plans/2026-10-05-chat-ui-implementation.md)。

> **聊天式采购助手后端收尾（2026-10-05，当前）**：普通中文聊天、无资料 revision 0、文字要求增删改候选、动态指标、上下文/记忆及真实 BGE-M3 + Milvus 混合检索已接入既有 Run/队列/API。候选须用户明确保存确认，正式审查按确认版本与完整覆盖执行；采购品类和指标名无白名单。最终离线 **1178 passed / 37 skipped / 网络0**，pip check/Ruff通过；独立实连 **2 passed**。真实队列四轮闭环完成，DeepSeek **7次请求**，索引/原文问答/正式审查/导出通过，测试资料已清理；含失败的收尾队列探针累计16次。实际迁移 **0016_assistant_runs (head)**，API/worker/indexer/cleaner已同步，宿主与关键容器源码hash一致。详见 [收尾及边界](docs/backend-completion-2026-10-05.md)、[接口与启动](docs/backend-assistant-api-2026-10-04.md)。Ollama当前使用本机11634；API/Ollama为前台进程，重启须按交接启动。**前端未改，下一步先讨论中文聊天界面规范与样式**。多品类离线回归不代表真实采购泛化准确率或生产高可用已达标。下方记录均按交付日期理解为阶段历史。

> **动态审查清单 B 后端主流程（2026-10-04，阶段历史）**：需求/订单候选 → 用户保存并确认 → 任意指标核验已接入现有 Agent、队列与 API。有限规则、原文/品目关联、版本绑定、批次恢复、来源追问、人工事实备选审计和中文导出已实现。见 [本轮交付及边界](docs/dynamic-checklists-b-2026-10-04.md)、[A/B/C 计划](docs/superpowers/plans/2026-10-04-dynamic-checklists.md)。最终离线 **1123 passed / 35 skipped**、网络 **0**；真实 MySQL **11 passed**，实际 head **0015_dynamic_agent**；合成资料 DeepSeek **8 次请求**。自然语言要求修改候选、普通聊天意图和动态语义检索路由仍待接入，B 未全部结束。未改前端或重启旧 API/Celery，网页尚未自动启用本轮代码；前端先讨论规范和样式。用户委托其他 AI 的检索部署未被操作。以下数字、迁移号及“待实现”按各阶段历史理解。

> **动态审查清单 A（2026-10-04，阶段历史）**：面向不同用户及任意采购品类，每次采购独立设置指标名称、数量、阈值与品目；示例品类不构成白名单。已实现六类规则契约、手工草稿/确认 API、不可变版本/部分确认累积、权限与来源/修订约束、Case 清理和旧八字段执行保护，见 [本步交付](docs/dynamic-checklists-a-2026-10-04.md)、[三个大步骤](docs/superpowers/plans/2026-10-04-dynamic-checklists.md)。完整离线 **924 passed / 32 skipped**、网络 **0**；真实 MySQL **8 passed**，实际 head **0014_dynamic_checklists**。独立审查发现的空白引用、历史快照状态及 MySQL 旧视图竞态已修复并复查。**B 的需求/订单自动候选与动态核验执行器尚未实现**；动态 Case 当前返回 checklist_execution_pending。未重启旧 API/Celery、改前端或调用真实模型，检索部署/验收由用户委托另一个 AI。以下记录按各模块与日期保留为历史。

> **上下文与分层记忆最新（2026-10-04）**：当前后端模块完成并审查。会话最多恢复八个真实答复阶段；MySQL 不可变 `task_memory` 绑定 Case/conversation/snapshot/source Run，保存完成/partial/待澄清阶段，`conversation_summaries` 保存带原消息与阶段 hash 的旧摘录；历史工具限定同会话/快照，不能提供采购 evidence_ref。session memory v3 兼容旧版，并验证资料角色/供应商/型号绑定；API 结果读取及事实修正同步支持 v3。账号偏好仅三个有限键，必须 `confirmed=true` 与 `expected_revision`，支持删除/到期；运行时及提交前检测失效。上下文按预算装配，Run 记录来源/版本/省略/输入指纹，已知用量在中断时保留。见 [交付、接口与实现边界](docs/context-memory-2026-10-04.md)、[已完成计划](docs/superpowers/plans/2026-10-04-context-memory.md)。最终离线 **893 passed/29 skipped**、网络尝试 **0**，真实 MySQL **3 passed**，迁移为 **0013_layered_memory (head)**。

> **分层记忆交付时的约束（历史）**：本轮未操作用户交给其他 AI 的 Ollama/BGE 部署，真实模型调用 0；API/Celery 未重启，前端未改。个人偏好现阶段保存并注入，正式答案仍由既有中文 renderer 生成；普通聊天、自然语言记忆入口、私有/共享会话选择和动态清单仍待实现。不声称已接 LangGraph 官方 checkpointer 或完成全节点续跑。下一模块实现用户手动设置/从需求订单识别的动态审查清单；聊天前端先讨论规范和样式。下方较早状态、迁移号和测试数字均为历史，不覆盖本次已验证结果，也不表示整个后端大步骤完成。

> **持久化索引最新（2026-10-03）**：MySQL 文档索引任务/manifest、租约与 generation 栅栏、宿主 worker、Case 删除对账、SQL 原文回读与成员权限、GET/POST indexes、生产 Run 检索注入已实现，见 [本轮交付](docs/durable-indexing-2026-10-03.md)。最终离线 **874 passed/27 skipped**、网络尝试 0；真实 MySQL **4 passed**、真实 Milvus 合成向量 **1 passed**，独立复审修复删除外键、actor 幂等清理、最终提交权限及升版孤立任务。数据库为 `0012_durable_indexes (head)`。未调用 Ollama/DeepSeek、未改 `.env` 或前端。Ollama 部署由另一个 AI 负责，完成后用户通知。向量开关默认关闭；API/Celery 未重启或重建，Docker worker SDK/本机 Embedding 连接与真实网页语义联调待安排，不能把源代码接入称为运行服务已启用。下一项继续上下文/记忆机制与动态审查清单，仍未勾选整个后端大步骤完成。下方记录均保留为历史。

> **中文混合检索组件最新（2026-10-03）**：原文偏移分块、Ollama BGE-M3 适配器、Milvus 显式 schema/Strong 回读、BM25Plus+RRF 及 Agent 注入已实现，见 [检索交付](docs/hybrid-retrieval-2026-10-03.md)。当前全量离线 **855 passed/26 skipped**、网络尝试 0；真实 Milvus 新适配器 **1 passed**，复审缺口已修复，Ruff/依赖检查通过。模型采用用户选择的本机 Ollama；真实语义对照待安装下载完成。当前网页 API 默认仍用既有 BM25；下一项是 MySQL 持久化索引任务、worker 栅栏及在线权限接入，随后继续聊天/记忆和动态指标，不将本轮组件称为后端大步骤 2 全部完成。未修改前端。

> **Milvus SDK/权限/真实探针（2026-10-03，最新）**：本轮完成 pymilvus 2.6.17 安装与依赖锁（dev/Windows 102 包、vector 闭包 17 包，worker 75 包暂不改）、本机 root 默认密码轮换及 docucheck_probe 专用库/账号/13 项范围权限。实连发现默认 public 可跨库读取结构等元数据，已在独占新栈撤销三项全局默认授权；跨库结构/内容及账号管理拒绝、匿名/旧默认凭据拒绝均实测。五条合成向量 Strong 检索/删除/清理完成，两库遗留集合为 0；初始化重复执行通过。全后端离线 **809 passed、25 skipped，网络尝试 0**；Milvus 单独实连 **3 passed**，Ruff/pip check/锁一致性通过。服务 healthy、观测约 153MiB/2GiB。见 [实连验收](docs/milvus-live-acceptance-2026-10-03.md)。这是服务与 SDK 基础验收，业务 RAG/Embedding/动态指标/新记忆尚未实现；Attu UI 尚未操作验证。下方未安装/未轮换/未实连为较早状态。

> **Milvus 新镜像启动补验（2026-10-03，最新）**：应用户要求，实际拉取官方 2.6.25，基础镜像以 digest 固定；独立 named volume 的新实例已 running/healthy，healthz 为 200 OK，匿名列表请求 code=1800 被拒绝，未重启/OOM。使用 compose.milvus.yaml + compose.milvus-smoke.yaml，2GiB 内存上限、禁用 swap 超额及自动重启；空载观测约 109MiB，仅证明小规模启动可行，不能作为索引/Embedding 容量结论。旧数据未改动。SDK、管理员口令轮换、Attu 实连、真实向量探针及业务 RAG 仍待完成。先前“未启动/资源阻止启动”被本条实测更新；详见 [启动补验](docs/milvus-foundation-2026-10-03.md)。

> **Milvus 基础接入（2026-10-03，当前进度）**：用户确认采用 Milvus 并开始实施机制方案，进入四个大步骤中的第二步后端。已写配置校验、默认无网络预览、显式合成向量探针和独立 Docker 部署文件；新增 31 项及既有定向回归合计 **81 passed**，网络尝试 0，Ruff/Compose 配置校验通过。实测宿主剩余约 0.49GiB、Docker 可见约 7.38GiB，旧 Milvus 日志为 etcd WAL/配置文件 I/O 错误；新栈未启动、SDK未安装、业务 RAG 尚未接通。见 [本步报告](docs/milvus-foundation-2026-10-03.md)。聊天/分层记忆、动态指标、混合检索与业务审查仍待实现；前端样式仍须先讨论。下方“仅设计/待审阅”为较早记录。

> **机制设计与 Agent 调研（2026-10-03）**：用户明确要求上下文、记忆、向量检索及手动/需求/订单生成指标，并已指定 Milvus。修订 [机制设计](docs/superpowers/specs/2026-10-03-context-memory-vector-criteria.md) 及 [个人开发者 Agent 方案](docs/superpowers/specs/2026-10-03-agent-architecture-research.md)：MySQL 分层记忆、Milvus Standalone + 已有 Attu、独立 Embedding + BM25Plus 混合检索、单 Agent + 受控审查子图和批次进度。本机 Attu 2.6.4，Milvus 2.6.7 旧实例已退出，Docker 可见约 7.38GiB，资源/连接尚待验证。除指定 Milvus 外的具体方案仍待审阅，新功能未实现；沿用四个大步骤，每步测试校验，不新增几十个确认点。

> **改版设计讨论（2026-10-03，当前任务）**：用户要求中文操作流程、普通聊天/RAG/上下文、多品类动态指标及前端审美改进，已明确选择“聊天为主，审查面板按需展开”。现状审查与 [改版草案](docs/superpowers/specs/2026-10-03-conversation-first-procurement-redesign.md) 已整理；具体视觉规范与实现边界待确认。当前应用仍为设备八字段版本，本轮没有修改应用代码。后续先确认设计，再按四个大步骤逐步实施并测试；此前联调成绩和独立验收/生产缺口保留，多品类需要新的验证。用户要求前端规范、样式讨论在代码修改之前。

> **最新源码联调（2026-10-03）**：第 1 项完成。独立 MySQL/Redis 栈与重建的 worker 已匹配当前后端源码；22 项服务测试全部通过、无跳过。真实开发质量 12/12 包、104/104 格、89/89 必要证据，危险满足 0、引用错误 0/244；固定五文件性能 10/10 达标、最高 62.125 秒。修复 Case 列表升版显示，前端最终 19 项、类型检查及构建通过；真实浏览器矩阵/导出、Bob 隔离、上传/修正/历史已核对。本轮上报 293 次模型调用，实际金额未知；独立人工封存仍 0。见 [本轮报告](docs/m5-current-version-integration-2026-10-03.md)。第 2–6 项仍待逐项执行；以下为历史记录。

> **交付后复查最新（2026-10-03）**：确认并修复 7 项代码问题，新增 41 项回归；原 Conda 和依赖锁对应的 Windows 隔离环境均 **765 passed、22 skipped**，网络尝试 0，pip check/Ruff 通过。包括历史规则结果读取/追问、八字段内存事实修正、混合版本正式标记、归档声明和离线边界。当前六个业务文件已修改，M5-B 真实模型成绩仍绑定原源码；本轮真实模型调用 0。新归档恢复 228 文件逐字节一致，资料错误 0；独立人工封存仍 0，生产/远程 CI 等条件保留。见 [复查报告](docs/m5-post-delivery-review-2026-10-03.md)。下方按日期保留历史结果。

> **M5-C 首次交付历史（2026-10-03）**：完整依赖锁、离线网络保护与 GitHub Actions、开发证据归档/恢复、启动和面试手册已交付。原 Conda、Windows 新环境、Linux 新环境各为 **724 passed、22 skipped**，网络尝试均为 0；前端 **18 passed**、类型检查/构建通过。最终 ZIP 的 227 项载荷已恢复并重验，12 包/104 格/38 原件无资产错误。业务源码与 M5-B 最终实测版相同，本轮新增真实模型调用为 0。独立人工封存仍为 0、实际账单未知；远程 CI、业务数据库灾备及完整生产发布尚未验收。见 [M5-C 交付报告](docs/m5-c-delivery-2026-10-03.md)、[复现手册](docs/reproduction.md)、[十分钟演示](docs/interview-demo.md)。下方较早阶段、测试数字和暂停点均按其日期理解。

> **M5-B历史（2026-10-03，进入 M5-C 前）**：最终fields-v4源码/worker同步，12包任务12/12、状态104/104、必要证据89/89、危险满足0、引用错误0/247；固定五文件性能9/10成功且≤120秒，第5次执行中断保留失败，跨两天恢复仅继续未开始任务。额外追问/取消/未知供应商/同源修正4项通过，修正139.44ms；最近完整后端690 passed、22 skipped，收尾检查通过。AI复核且作者已见，独立封存0、实际费用未知；该时点完成范围是M5-B开发验证，M5-C当时尚未开始。下方按日期保留历史结果和暂停点；它们不撤销用户已委托的AI复核与后续执行授权。见 [最终开发验证报告](docs/m5-b-development-validation-2026-10-03.md)。

开始工作前，先阅读本文件、`README.md` 和 `docs/development-roadmap.md`，再检查 Git 状态与实际文件。设计目标、已实现功能和实测结果要分开描述；不要把本文当成代码已经完成的证明。

> **M5-B历史（2026-10-02，早期开发版本）**：本机HTTPS信任、交期规则升级及历史修正兼容、Agent安全诊断、相邻内存标签/值和SSD检索均已修复。真实新两供应商校准16/16；固定v2五文件性能10/10成功且≤120秒。首轮12包仍为状态47/104、必要证据44/89，质量未达标；单包和性能成功不替换首轮分数。用户已确认采购承诺口径，当时代码与运行服务为fields-v3，完整回归626 passed、22 skipped。AI复核、作者已见，独立封存仍0；M5-B继续，未进入M5-C。详见 [本轮传输、校准及验收记录](docs/m5-b-transport-and-calibration-2026-10-02.md)。旧规则/历史报告不回写，v3全集和十次性能尚未复测；可选容量、套装总价、其他型号说明的全量覆盖仍需处理。无需用户再填写已委托的复核表，下方较早暂停点为历史。

> **M5-A 复核工具（2026-10-02，最新）**：新增 `evals.corpus_review` 准备版本绑定的空白逐格复核/确认、检查人工声明及冻结记录；未复核/漏格/状态错误/版本变更/污染留出/覆盖记录均拒绝冻结。修复候选清单为null或条目类型错误时抛异常。新增22项；定向43项、全后端 **518 passed, 22 skipped**，Ruff/依赖通过。实际目录 `data/m5-review/` 为pending104/reviewed0/sealed0，拒绝封存且无freeze.json；临时测试的虚构确认不算真实复核。见 [交付与操作](docs/m5-a-review-gate-2026-10-02.md)。M5-A代码/工具准备已补齐，仍待人真实核对原件、确认至少六包冻结；“继续”或选择复核安排不是复核回执。未调用模型/实时解析/数据库/Docker/前端，不进入M5-B。外部冻结声明表达当前批准状态，gold生成标记保持原始来源记录；本机声明不是身份认证或数字签名。下方为历史。

> **M5-A 候选准备（2026-10-01，本轮最新）**：按用户要求继续查GitHub/社区，检查五个仓库目录及采购数据卡；未找到直接适用的完整配套八字段集，见 [候选交付与检索记录](docs/m5-a-candidate-corpus-2026-10-01.md)。已生成12个新合成候选包、13家/104格拟定gold（6校准、6留出候选）；原件与gold隔离、确定性生成、逐格原文/范围/划分/哈希校验和人工复核工作表已备妥。38文件实际上传预检通过，36页PDF渲染检查；新增21项通过，全后端 **496 passed, 22 skipped**，Ruff/依赖通过。修复校验工具父目录链接越界。作者已见所有候选，不能冒充未见集；**reviewed=0、sealed=0，M5-A整体仍待独立人工复核及封存**。继续先完成这项，不能把“继续”当复核回执；本轮无真实模型/MinerU/Docker/前端操作，不进入M5-B。下方记录为历史。

> **M5-A 当前进度（2026-10-01）**：用户确认M5分三个大步骤，已开始资料准备，并要求先搜索公开材料。已下载7份公开PDF/ZIP原件并登记哈希、日期、来源与限制；`evals.source_assets`显式下载或默认离线校验，不能读取gold或宣称业务达标。新增16项测试，后端 **475 passed, 22 skipped**，Ruff/依赖通过。详见 [M5计划](docs/m5-large-step-plan-2026-10-01.md)和 [来源调查](docs/m5-a-public-source-research-2026-10-01.md)。M5-A整体未完成：长PDF/不同标包/缺报价规格等需整理，12包104格与6包独立封存尚未具备。候选生成与人工独立复核、封存要分开；已用资料不得改名冒充未见集。本轮无模型、实时MinerU、Docker或前端操作，不进入M5-B；每个大步骤完成后测试、审查、补缺并向用户报告再继续。下方阶段进度为历史记录。

> **M5 前审查（2026-10-01）**：核心链路审查修复完成，见 [审查报告](docs/pre-m5-code-review-2026-10-01.md)。未知明确供应商先澄清；同快照恢复的结果必须由已验证事实重算一致，缺失事实的旧会话只恢复对话上下文；前端异步操作绑定原 Case/Run，迟到响应不得覆盖新页面；追问优先延续选定 Run 的会话；上传按实际 ASGI 字节限额。后端 **459 passed, 22 skipped**、MySQL **13 passed**、前端 **18 passed**，构建/静态/依赖/迁移通过。本轮未调用 DeepSeek。M5 尚未开始；下一阶段按独立封存样本、真实质量/成本/性能验收、CI 与交付三个大步骤推进；公网部署和大规模使用仍受报告中的门槛约束。

> **M4 当前状态（2026-10-01）**：用户确认的“专业审查工作台”已完成 M4-A/B/C，以及 M4-D 本地合成样例范围内的同源 JSON/XLSX 导出和端到端验收。真实 MySQL 已迁到 `0011_document_model_binding`；OIDC 浏览器验证了单家 8 格、三家 24 格、追问、原件、修正/历史及导出，见 [M4-C 记录](docs/m4-c-agent-workbench-2026-10-01.md)和 [M4-D 交付记录](docs/m4-d-export-delivery-2026-10-01.md)。后端全量 `444 passed, 22 skipped`，前端 `12 passed`；本轮使用无网络脚本 Provider 和原件哈希对应的 PDF 解析缓存。实时 MinerU 新文件质量、真实 DeepSeek 页面效果、独立资料集与生产部署仍待 M5 验收。真实模型调用前须说明发送范围和费用，运行 Docker/前端前先告知用户。

> **M4 前生产准备复审（2026-09-30）**：M3 的结论仅覆盖本地后端功能。复审发现共享部署前的身份/Case 授权缺口，并修复 Run 互斥、提问长度、取消传播、旧快照排队状态及未配置 PDF 解析器的边界。最终回归结果和生产门槛以 [M0–M3 生产准备审查](docs/pre-m4-production-review-2026-09-30.md) 为准。在确定部署形态与权限契约前不将 API 暴露到非本机地址；开始 M4 前端代码前仍先讨论规范和样式。

> **M3 当前结论（2026-09-30）**：本地后端里程碑的完成条件已验收。会话和消息可分页读取，真实 MySQL 新 API 进程能恢复历史和幂等结果；真实 MinerU 样例 PDF 上传解析与修正快照隔离 Celery Run 已通过。后端全量 **416 passed, 19 skipped**，MySQL 定向 **7 passed**，两项后台闭环各 **1 passed**；Ruff、依赖、迁移检查通过。隔离测试未调用 DeepSeek。详情及未覆盖边界见 [M3 验收与审查](docs/m3-acceptance-2026-09-30.md)。M4 前端开始前仍须先讨论规范与样式。

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

**前端暂停点（已履行）。** 2026-09-30 用户选择文字讨论和“专业审查工作台”，确认 [M4 界面方案](docs/superpowers/specs/2026-09-30-m4-workbench-design.md) 并明确同意进入 M4-B。此后可按已批准方案继续 M4 前端实现；若改变主要设计方向，再与用户讨论。运行 Docker 或前端前先告知用户。

## 产品范围

本项目是采购资料初审 Agent 工作台；M4 首版以本地单组织、多演示账号协作验证项目/Case 权限。一次任务包含 1 份笔记本采购需求，以及 1～3 家供应商各 1 份报价和 1 份规格书；首版只接收可提取文本的 PDF 和普通 XLSX。用户上传后提问，单 Agent 根据问题及工具结果动态决定检索、抽取、核验、继续取证或澄清。比较八项：内存、存储、屏幕尺寸、重量、质保、采购数量、单价、交付期限。产品型号用于关联资料，不计入八项。

当前唯一 Agent 架构基线为 `docs/superpowers/specs/2026-09-24-agent-first-design.md`，覆盖 Provider、状态图、工具、上下文、RAG、预算、恢复和评测；同日两份 v0.1 Agent 文档仅作历史。具体任务在 `docs/superpowers/plans/2026-09-24-agent-mvp.md`，执行节奏以 `docs/step-by-step-plan.md` 为准。Provider 的真实单次工具调用、三批模拟资料抽取与 Agent 连续两轮对话均已有用户反馈和本机记录；M1 的最终范围与限制见验收小结。

业务结论只有：满足、不满足、资料冲突、证据不足、待人工确认。文件处理状态（如 partial、failed、interrupted）与业务结论分开。解析失败或来源未覆盖时，不得把字段当成缺失；没有可定位证据时不得判“满足”。最终采购决定由人作出。

首版上限：单文件 10 MiB、PDF 全任务 30 页；XLSX 每文件最多 5 表、每表 1,000 行/30 列、总非空单元格 20,000、单元格 2,000 字符、解压后总量 50 MiB。质量目标是状态准确率 ≥85%、确定性结论的必要证据定位率 ≥95%、明确不达标却判满足为 0；这些均为待验收目标。

## 技术栈与运行方式

| 层 | 约定 |
| --- | --- |
| 本机 Python | Miniconda `docucheck` 环境，Python 3.12；项目旁旧 uv `.venv` 的 Python 3.13 不能代替。M5-C 新建的 Python 3.12 隔离 venv 仅用于安装/CI 复现验证，主开发环境仍为 Conda |
| 前端 | 当前为 Vue 3、TypeScript、Vite、Keycloak JS、PDF.js；暂未引入 Element Plus、Vue Router 或 Pinia |
| API | FastAPI、Uvicorn、Pydantic 2 |
| 数据与迁移 | MySQL 8.4 / InnoDB、SQLAlchemy 2、PyMySQL、Alembic |
| 异步任务 | Celery 5、Redis broker；MySQL 保存权威业务状态 |
| 解析 | MinerU 4.0.7 为 PDF 开发验证候选；XLSX 使用 openpyxl、defusedxml |
| Agent 与模型 | LangGraph 1.x 单 Agent；LangChain 模型/工具接口 + 配置化 Provider；模型理解问题、选择受控工具并提出带证据引用的候选事实 |
| 判定与质量 | Python 规则引擎 + Decimal；pytest、Ruff、前端类型检查/Vitest、Playwright、GitHub Actions |

Windows 上运行 API 和 Vite；Docker Compose 运行 MySQL、Redis 和 Linux Celery worker。项目 MySQL 主机端口由 `.env` 的 `DOCUCHECK_MYSQL_PORT` 决定（默认 `3307`；本机 Windows 保留该端口，现用 `13307`），容器内仍为 `3306`；Redis 映射到 `127.0.0.1:6379`。本机 `data/` 在 worker 内是 `/data`；数据库只存相对 `storage_key`。本地凭据写入被忽略的 `.env`，不得提交。启动与检查命令见 `README.md`。

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

当前已存在：根目录 `compose.yaml`、`pyproject.toml`；`backend/environment.yml`、`backend/app/`（Celery 健康检查、PDF/XLSX 解析、`evidence/`、`providers/`、`agent/`、`extraction/`、`rules/`、`db/`、`api/`）、`backend/tests/`、`backend/migrations/`；`frontend/`、`examples/dev-pack-001/` 与 `examples/dev-pack-003/`；`evals/`、`docs/`、`scripts/`。M4-C 的工作台已在本地 OIDC/MySQL 浏览器流程验收；M2 八字段业务模块此前还通过用户执行的真实模型单家、三家开发包探针。

业务模块沿用 `backend/app/`，数据库 repository/service 实际在 `db/`；迁移、前端、评测及 `.github/workflows/offline-ci.yml` 已存在。M5-C 增加 `scripts/offline_guard.py`、`scripts/verify_offline.py`、`evals/delivery_bundle.py` 和完整依赖锁。正式可安装包迁移另行处理，不仅为匹配旧目标树移动现有代码。

## API 契约（部分已实现，详细进度见 M3 验收记录）

- 前缀 `/api/v1`。Case 内容修改和问题提交带 `expected_revision`，全新 Case 创建不要求；上传、创建运行、修正、重试和澄清恢复带 `Idempotency-Key`。同键异载荷返回 HTTP 409。
- 资源：`/projects`、`/cases`、Case 下供应商/文档、`/cases/{id}/conversations`、`/conversations/{id}/messages`、`/cases/{id}/runs`、`/agent-runs/{id}` 及其 events/results/facts/cancel/retry/resume/export、`/cases/{id}/corrections`。指定快照证据为 `/cases/{id}/snapshots/{snapshot_id}/evidence/{evidence_id}`，原件为 `/cases/{id}/snapshots/{snapshot_id}/documents/{document_id}/original`，两者均校验 Case 权限和快照归属。具体契约以实际 OpenAPI 与前端 `api.ts` 为准；旧独立 `/questions`、`/runs`、`/documents/{id}/content` 未实现，不建立兼容层。
- 运行需支持取消与显式重试；健康接口为 `/health/live`、`/health/ready`。错误响应包含 `error.code`、`error.message`、`error.details`、`error.request_id`。文件读取接口只接受服务端生成的 ID，不接受客户端文件系统路径。
- 创建 Run 与 Outbox 在同一数据库事务内；worker 按 run/attempt 幂等处理，不承诺 exactly-once。导出读取不可变运行快照，和页面显示一致。
- Run 查询返回活跃 attempt 的 `outcome_unknown`。`worker_lost` 的 Run 为 `interrupted`，用户须显式 retry；系统不能自动重发可能已经到达模型服务的请求。

## 计划与当前进度

详细计划和各阶段验收条件以 `docs/development-roadmap.md` 为准；阶段 0 记录见 `docs/superpowers/plans/2026-09-24-phase-0-foundation.md`，环境实测见 `docs/environment/2026-09-24.md`。

1. **M0（基础资产已验证）**：Conda、模拟样例、MinerU 开发验证、Docker 服务、解析代码；M5-C 已补齐应用依赖锁与隔离安装验证，任意复杂 PDF 的完整语义覆盖仍不承诺。
2. **M1（模拟包范围已验收）**：最小真实 Agent：版本化证据、Provider、词法检索/原文工具、内存抽取规则、LangGraph、CLI 两轮对话及事件。验收内存冲突与来源追问。
3. **M2（开发范围已验证）**：八字段、全量适用资料覆盖、三家供应商、上下文压缩、深入模式、澄清和来源答复；后续 M5-B 补齐多型号/口径覆盖并做真实开发集测量。
4. **M3（本地后端链路已验证）**：持久化会话 API、MySQL 迁移、Outbox/Linux 任务、幂等、取消、中断重试、解析/删除清理与证据约束修正。
5. **M4（已确认设计的开发工作台已验证）**：Vue 上传对话工作台、来源、修正、历史、报告；现有 18 项前端测试和构建已在 M5-C 新安装环境再验。
6. **M5（开发交付已完成，独立验收待完成）**：AI 复核开发集、真实模型质量/性能报告、完整锁、离线 CI 配置、归档恢复及面试手册已交付。独立人工封存、实际账单、远程 CI 与生产灾备/发布验收保留未完成。

当前 PDF 校验覆盖 pypdf 可提取文字，并对嵌入图片、实际绘制的矢量内容、注释和疑似无框列布局保守标记未验证；`complete` 尚不能证明任意复杂 PDF 的语义关系均可恢复。XLSX 已检查合并单元格、错误范围声明、实际引用的绘图、常规及线程批注、旧式绘图、隐藏行列/工作表、页眉页脚和未知关系引用；任务级文件与页数限额已在入口执行。步骤 3 已选 `deepseek-flash`，凭据仅本地配置；真实 DeepSeek 的合成开发包探针已通过，但不能代替独立资料集质量及费用、时延验收。

首版质量和性能数字都是目标，尚无正式验收结果。原始需求与架构细节在用户提供的《DocuCheck-AI开发协作交接文档-v1.0.md》中；如本文与实时代码不一致，以实际代码和最新经确认的需求为准，并修正文档。
