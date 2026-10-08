# 交接修复后的实连验收计划

> 执行方式：使用 executing-plans，按下列检查项执行并在交付前使用 verification-before-completion。用户已授权继续；每步完成后独立提交本地 Git。

**目标：** 将前三组修复及 R13 静态证据与当前运行服务核对，补齐本地实连证据，不将历史成绩当成本次成绩。

**架构：** 保持现有模块化单体、OIDC、MySQL、Redis/Celery 和 Vue。数据库回归使用 UUID 命名临时 schema，与正式 dispatcher 隔离；页面和真实队列抽样使用本轮创建的合成采购任务。清理仅针对本轮拥有的测试资源。

**技术栈：** pytest、Alembic、SQLAlchemy、Docker Compose、OIDC PKCE、Vue、DeepSeek、本机 BGE-M3/Milvus。

## 一、运行版本和数据库

- [x] 记录基准 Git、实际容器镜像、后端文件哈希和 HTTP 健康状态；检查活跃 Run，保留既有 waiting_user 任务。
- [x] 告知用户后同步 worker/dispatcher/indexer/cleaner 与 API，保留数据库及既有资料。
- [x] 在独立临时 MySQL schema 执行迁移至0016及16个无外部模型测试模块，最终29项通过，保存失败/跳过/清理记录。

2026-10-08第一部分交付：实连发现批次旧RR视图缺陷；修复前3项确定性回归失败，补充跨Case死锁回归也失败，再修复后29项通过。补齐5项离线重试边界测试，最终全量1248 passed/41 skipped/网络0。详见 [交付记录](../../repair-live-database-2026-10-08.md)。第二部分仍未开始；前端5173被系统保留，先处理端口与OIDC回调。

## 二、登录和产品链路

- [x] 第二部分A（2026-10-08）：修复Windows保留5173导致的Vite启动失败，共用根.env端口15173、strictPort、既有OIDC客户端登录/退出回调同步；前端已启动，中文PKCE登录页与拒绝边界通过。见[本步记录](../../repair-local-login-2026-10-08.md)。等待用户本机登录，以下完整链路不勾选。
- [x] 用真实 OIDC 登录与浏览器检查清单编辑保存确认、候选状态、记忆有效期、报告/原文/导出及人工事实修正；正向修正的初始报告用明确的测试Provider夹具准备，其余真实模型抽样另项记录。没有实际检查的场景明确保留。
- [x] 用新建合成资料验证真实 HTTP/队列/模型抽样；调用前说明目的、发送数据和费用未知，记录请求数、token、失败及版本。
- [x] 恢复既有 Milvus 栈后检查 BGE/Milvus 实连；失败和词法降级记录保留。第二部分H最终五项实连通过，持久化SQL层为SQLite，不扩大到本轮MySQL/网页/队列RAG验收。

2026-10-08第二部分B（部分完成）：用户登录后已实测清单编辑/保存/部分确认、升版重确认、候选历史与记忆有效期保存；历史合成报告/原文/Excel及JSON下载一致性通过。发现历史候选继承当前提示/逐项issues/确认徽章，及非设备资料型号文案，均已修复，前端148项/构建、后端定向23项/网络0和独立复核通过。新资料真实模型候选1次成功，完整审查原请求及一次显式重试均connection_error（用量未知），不勾选上方整项。实际退出、新报告修正与BGE/Milvus仍待执行。下一项先定位生成请求连接原因，再继续剩余闭环。详见[本步记录](../../repair-browser-product-2026-10-08.md)。

2026-10-08第二部分C（待应用）：同worker一次诊断复现SSL验证失败，握手链根与Windows已信任Kaspersky根SHA-256精确匹配；证书信任修复需用户明确确认。已准备安全TLS错误分类及worker-only可选只读bundle overlay，修正独立复核发现的共享data写入别名，定向138项/网络0、Ruff/pip check/Compose隔离检查通过。尚未应用信任配置或重建worker，不勾选真实审查闭环。见[根因及应用/回滚方案](../../repair-model-tls-2026-10-08.md)。

2026-10-08第二部分C应用：用户已确认本机根信任，worker只读CA配置生效、healthy，118源码文件一致、其余6容器未重启，既有waiting_user保留。标准在线构建因Docker Hub超时受阻，改用已验证缓存运行环境更新应用；SDK TLS检查及一次最小合成模型工具探针成功（304输入/35输出token）。本轮定向138项/网络0、Ruff和容器pip check通过。网页显式重试遇OIDC会话过期，数据库未新增attempt，苹果审查仍partial；等待重新登录后继续，不勾选整个业务闭环或BGE/Milvus。见[本步应用记录](../../repair-model-tls-applied-2026-10-08.md)。

2026-10-08第二部分D：苹果新资料审查completed，两项正式满足、批次1/1；原文定位和本次Excel/JSON/数据库逐项一致性、原件hash通过。修复模型只引用值格/单位改写的抽取提示缺口，增加有限拒绝原因，独立复核的型号绑定遗漏已关闭。最终后端1268 passed/41 skipped/网络0、定向155项/Ruff/镜像检查通过。模型7次请求（覆盖失败3、诊断1、成功3），22744输入/1066输出token；最终提示补充后未额外调用模型。真实HTTP/队列/模型抽样已勾选，第一项仍保留新报告人工修正提交/实际退出，BGE/Milvus另行继续。见[本步交付](../../repair-apple-review-live-2026-10-08.md)。

2026-10-08第二部分E：浏览器26箱无原文支持修正被拒绝，重读恢复正式结果；真实MySQL四次快照一致、审计0、既有waiting_user保留。主动退出/后退/重访均停留中文登录页，三个匿名报告接口401。后端定向23项/网络0、前端148项/构建通过，模型0次。正向修正有自动化覆盖但未做本轮浏览器成功提交，第一整项继续未勾选；后续用支持双值的独立合成资料补齐正向提交，再继续BGE/Milvus。见[本步记录](../../repair-correction-logout-live-2026-10-08.md)。

2026-10-08第二部分F：同源12/8箱正向修正通过真实OIDC页面/API/MySQL，旧值保留、冲突非正式、审计/聊天/检查点及导出一致，原件hash通过；初始报告是隔离Outbox的测试Provider夹具，两次各3次模拟调用，真实模型0次。实测发现并修复待复核误称抽取未完成：红阶段2失败，绿阶段定向31项/全量1269 passed41 skipped网络0，Ruff/pip check通过。API已按OIDC脚本重启，首次裸命令漏OIDC参数立即纠正并记录；worker未同步1文件不声称全容器一致。产品页面项现已勾选，下一项BGE/Milvus实连，统一部署带入worker差异。见[本步记录](../../repair-correction-positive-live-2026-10-08.md)。

2026-10-08第二部分G（部分完成）：Ollama/BGE已恢复，digest与索引配置一致，1024维归一化及indexer宿主桥接HTTP200通过。既有Milvus两次启动均exit134，直接原因Session.Init遇etcd首次选主leader changed；底层触发原因未确定，未重建/清卷/改权限。真实BGE+真实SDK失败的组件诊断两条均显式BM25降级，receipt为合成进程内指纹，不证明已写入向量、SQL或网页端到端。本诊断Embedding3次/DeepSeek0次；新增ready manifest依赖故障与原文守卫两项，最终相关92项/网络0、Ruff/pip check通过。三组向量成功路径实连未运行，整项保持未勾选；下一项先解决启动再验证持久化RAG，5条failed索引须后续核对后显式重试，worker1文件差异仍保留。见[本步记录](../../repair-rag-reverification-2026-10-08.md)。

2026-10-08第二部分H：确认内置etcd启动源码未等待选主就绪，改用同版本官方etcd、健康门禁与原卷子目录；一次性storage-init解决新卷目录缺失。初次真实测试1失败/4通过，发现Milvus重启重新授予public跨库元数据权限；关闭enablePublicPrivilege并仅撤销既定三项默认授权，重启后最终5项真实Milvus/BGE通过，public为空、专用角色仅本库。原卷149MB停机备份保留、原集合清单一致、临时测试集合和新卷已清理。Embedding累计6次、DeepSeek0次；完整离线1274/41/网络0，最后新增部署用例另由最终22项验证，Ruff/pip/Compose通过。本轮持久化BGE测试SQL为SQLite，不能称MySQL/网页/队列全链路；5条failed索引未重试、worker1文件差异保留。向量实连检查可关闭，后续核对失败索引并统一部署。见[本步记录](../../repair-milvus-startup-2026-10-08.md)。

## 三、交付

2026-10-08第二部分I：核对五条failed索引均为三个Case当前资料、解析complete、target一致及项目owner成员范围后，调用既有schedule显式重试；Docker indexer五条均一次ready，11分块expected=ingested、原件hash一致。三个隔离诊断会话/Run通过真实MySQL清单、本机BGE、Milvus Strong及原文再读，历史generation不存在、未知供应商和终态Run访问拒绝。查询Embedding6次、DeepSeek0次，后台索引请求未独立计量；Outbox同事务标为已处理的测试事件，不触发业务worker。本步离线55项/网络0、MySQL并发1项、Ruff/pip通过；运维诊断不扩大为浏览器/模型回答验收。waiting_user保留，worker118文件比较仍有dynamic_repository.py一处既有差异，下一步统一部署。见[本步记录](../../repair-index-recovery-2026-10-08.md)。

- [x] 第一部分记录本次证据、范围、未通过项及源码/服务版本，更新 CLAUDE.md、README.md 和路线图入口；第二部分另行补充。
- [x] 第一部分对业务修复执行对应MySQL、离线和定向安全回归；第二部分如产生改动继续执行对应门禁。
- [x] 第一部分核对 diff、敏感信息和工作区归属，纳入独立本地 Git 提交；实际提交号以日志及交付回复为准。第二部分完成后另行提交。远程仓库未配置，不声称已推送。

## 边界

本轮不改视觉规范，不重新生成身份凭据，不删除用户资料，不调整其他独立验证栈。临时数据和原始日志存放被忽略的 `data/verification/`；提交可复核的脱敏结果。独立人工封存、真实多品类准确率、费用账单、远程 CI、生产容量与灾备仍按既有发布门槛处理。
