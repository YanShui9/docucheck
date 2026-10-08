# 失败索引恢复与 MySQL 持久检索验收

2026-10-08，基于 `1f82211`。本步完成既有五条失败索引的显式重试，并验证真实 MySQL 清单、本机 BGE-M3、Milvus 与 SQL 原文的检索链路。使用现有业务实现，无应用代码、数据库结构或前端修改。

## 恢复范围与执行结果

恢复前逐条核对：五份 XLSX 均已 complete 解析、属于所在 Case 当前 snapshot/revision、未请求删除；索引 target 与当前固定 BGE revision 一致。涉及三个 Case、两个项目。五条任务此前均 attempts=3、fence=3、failed，错误为 `index_service_unavailable`；本记录不据此追溯证明每次历史失败的具体底层原因。

运维脚本检查项目 owner 成员存在、通过 `WorkspaceRepository.require_case(..., "write")`，加载当前解析快照后，调用现有 `IndexRepository.schedule(..., retry_failed=True)`，保留 revision 锁和 CAS 校验。这里是用户授权的运维恢复，使用项目已有 owner key 校验资源范围；没有通过登录令牌发起 HTTP 请求，不能称为本轮 OIDC 或浏览器权限验收。

现有 `docucheck-indexer-1` 轮询进程完成真实索引写入；未启动第二个索引器或重启服务。结果如下：

| Case | 当前 revision | 资料数 | 写入原文分块 | 最终状态 |
| --- | --- | --- | --- | --- |
| `832da6f4a512476c86a2cfc79a7fe30e` | 2 | 2 | 8 | 全部 ready |
| `94e5b74b1f6947c4b07a0b87f732e037` | 1 | 1 | 1 | ready |
| `c4fbfe1f45814d0db5cfbec8b4de6365` | 2 | 2 | 2 | 全部 ready |

五条任务均重试一次成功，fence 从3经重新调度和领取增至5；每条获得新的 manifest/generation。所有 manifest 均 ready、expected_count=ingested_count>0，合计11。五份原件现有字节 SHA-256 与恢复前数据库记录一致。

## 真实检索及边界

每个 Case 新建一个标明“运维检索验收”的独立会话和 Run，使用真实 requester 项目成员及当前 snapshot。诊断 Run 的 Outbox 在 submit 同一事务内设为 `test.index-recovery / delivered`，普通 dispatcher 仅领取 `run.dispatch` 的 pending/sending 事件，因此不会触发生成模型或业务审查。三条诊断 Run 均以明确“未生成采购结论”的文字结束，保留在数据库作为本次验收痕迹。

每个新 Run 从已提交 MySQL manifest 创建全新 `persisted_retriever`，验证：

- 重建的 chunk receipt 与清单指纹一致，Milvus Strong 查询逐块确认当前 chunk ID/fingerprint。
- 两条中文查询均使用 `ManagedHybrid`，dense_candidates>0、degraded_reason=null，结果包含 dense 通道；三个 Case 合计六次查询。
- 每条查询的引用原文都通过 SQL 再读取校验，并只属于当前 Case/snapshot 的资料及选定供应商/需求角色；保存 sheet/cell 和原文 hash。
- 当前资料已有 deleted manifest 对应的历史 generation 在 Milvus 中均不存在。这只覆盖这五份资料的历史 generation，不扩大为全库审计。
- 不存在的供应商在 Embedding 之前以 `supplier_scope` 拒绝；Run completed 后重用检索器以 `source_access_changed` 拒绝，均未增加 Embedding 请求。
- 查询始终 `complete_coverage=false`。防水认证查询仍返回相近资料，不能据检索结果推断“无防水要求”，也没有生成无答案判断；正式完整审查仍需既有全量抽取和确定性核验。

查询诊断阶段准确计量本机 Embedding **6次**，DeepSeek **0次**；诊断 Python 进程的网络守卫只允许 loopback，外部连接尝试0。后台索引阶段的 Embedding 请求未独立计量，不能将6称为本步或全机请求总数。实际索引创建由 Docker 后台进程完成，诊断查询在宿主 Conda 环境直接调用生产检索组件，不能称为浏览器聊天、真实模型回答或完整队列审查验收。

## 回归与运行状态

本次相关离线回归 **55 passed / 网络尝试0**，覆盖索引重试/租约/generation、索引器、持久检索、混合检索及 Agent、Outbox 与 workspace。真实 MySQL 并发领取、过期回写拒绝和清理定位保留测试 **1 passed**，用独立合成 Case 并 finally 清理。Ruff 与 pip check 通过。应用代码未改，本步未重复全量后端、前端构建或真实模型审查；上步全量结果按原记录保留。

验收末五条任务仍 ready，API live/ready 200、匿名 `/api/v1/me` 401；indexer、worker、MySQL、Milvus 和etcd均 healthy。既有 `d2afc28c48d547688e72d1864947cb5f / waiting_user` 在恢复及诊断前后一致。三个诊断会话不借用原用户会话。

复核118个 worker Python 文件，按 UTF-8 通用换行规范化比较，仍只有 `backend/app/db/dynamic_repository.py` 与仓库不同：前一步修正文案变更尚未同步到 worker。本步没有处理部署差异。首次采集误用 `/app` 得到空清单的两个临时结果保留为无效诊断记录；正式证据使用导入模块定位 `/opt/docucheck`，强制两侧文件清单一致后生成的 `runtime-verified.json`。

## 证据、复现与后续

[脱敏验收JSON](repair-index-recovery-evidence-2026-10-08.json)保留调度、前后状态、generation、查询诊断、检查命令、运行状态和原始文件hash。原始脚本、日志、完整ID及查询引用位于忽略目录 `data/verification/index-recovery-live-2026-10-08/`，不提交原件或凭据。

独立只读复核重新计算21项文件hash，全部匹配；核对前后五条状态、三条诊断Run、生产检索/原文及Outbox实现、55+1测试记录和118文件运行比较，无阻断问题。确认本轮Strong检查覆盖这五份资料的15个历史manifest generation，均不存在。复核没有独立重跑网络/模型/恢复操作，结论限定于本地证据与代码审阅。

这是一份已有资料的恢复操作记录，不能直接重跑恢复脚本重复创建会话。今后恢复应先读取当前状态，核对当前 revision/target/权限，使用现有接口 `POST /api/v1/cases/{case_id}/snapshots/{snapshot_id}/indexes`，请求包含当前 `expected_revision` 和 `retry_failed=true`，等待 GET 同路径返回 ready；不要直接修改状态字段。权限、scope和原文约束依旧由业务实现承担。

下一步统一部署 worker 的既有修正变更，再复核运行源码一致性。独立封存集、多品类准确率、无答案拒答和生产容量/灾备仍按原发布门槛处理。
