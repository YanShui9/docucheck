# 持久化索引与 API 接入执行计划

**Goal:** 将原文索引从进程内演示接到 SQL 持久化任务、后台 worker 与已有 Agent Run。

**Status（2026-10-03）:** 本计划范围代码及验证完成；最终离线 874 passed/27 skipped、0 网络请求，真实 MySQL 4 passed、Milvus 合成向量 1 passed。数据库升级至 0012。实际模型部署、运行服务镜像同步与网页真实语义验收仍在计划范围外，详见 [交付记录](../../durable-indexing-2026-10-03.md)。

**Architecture:** 沿用已批准的机制方案和既有 Case/解析租约。解析事务登记 document_index_jobs；每次领取创建不可变写入 generation 的 index_manifests；Strong 验证后以租约/版本条件提交 ready。查询只接受 SQL 当前 ready generation，删除与失败通过持久化 tombstone 重复清理，不假设 SQL 与 Milvus 有跨库事务。

**Tech Stack:** SQLAlchemy Core/Alembic/MySQL，现有 Milvus/Retriever/LangGraph，Windows 宿主轮询 worker（与本机 Embedding/GPU 运行条件匹配）。

用户已授权本步，Ollama/BGE 部署由用户另行安排。本轮不安装、下载、改写 Ollama 配置或执行模型服务探针。采用离线 Embedding/向量替身；可独立验证 SQL 与真 Milvus。保留当前工作区，不覆盖其他 Agent 的部署文件，不提交。

## A. 事务与索引版本

- [x] 新增迁移 0012：document_index_jobs、index_manifests、agent_runs.requester_key；复合文档/清单范围外键、任务唯一文档键、状态/数量约束及领取索引。manifest 不依赖会被清除的 snapshot，保留 locator。
- [x] 解析成功的同一事务登记任务；人工修正复制原文时也登记新文档任务。API 幂等补登记历史已解析资料。
- [x] Case-first 锁序、有限领取租约、续租、最多三次自动重试、过期回收；旧 claim 与旧快照不能提交 ready。
- [x] chunk/schema/filter 增加 generation；模型/策略和写入代际均隔离。旧 worker 迟到 upsert 不能混入当前检索。

## B. 后台处理、权限与清理

- [x] 逐文档加载权威 SQL 原文、批量向量化、upsert、Strong 回读并以 fenced SQL 更新 ready。
- [x] 独立宿主 worker CLI 默认预览，显式执行轮询任务；不在 API 请求内向量化整份资料。用 fake factories 演练重启恢复、失败重试和幂等。
- [x] Case 删除同事务撤销领取并把各 manifest 标为 deleting；元数据不依赖已删除的原件行。清理按精确 generation 删除并 Strong 验证；保留定时再查的 tombstone，捕捉跨库未知结果的迟到写入。
- [x] SQL 回读来源及在线权限/资料版本复核；Agent Run 记录发起人 key，发送模型/最终提交前拒绝撤权或删除后的资料读取。

## C. API、验收与运行说明

- [x] 已授权 Case/snapshot 的 GET indexes 与 POST indexes 返回状态/就绪数量和重试结果，不暴露内部凭据或可执行 filter。
- [x] 既有 Agent Run 注入 SQL 验证的混合检索器；开关默认关闭，只有显式设置已确认模型 revision 后启用。未 ready/服务失败提供词法降级诊断，不作缺失结论。
- [x] 当前身份边界和过期输入测试、SQLite 文件重启/迁移比较、MySQL opt-in 与真 Milvus generation 测试；完整离线回归、Ruff、pip check、diff check及只读复审。
- [x] 更新 CLAUDE/README/roadmap 与交付文档，记录部署仍待用户完成，给出迁移/worker启动步骤。不要把离线替身结果称为真实 BGE 语义验收。

检查命令：

```powershell
python.exe -m pytest -p scripts.offline_guard backend/tests/test_index_repository.py backend/tests/test_index_worker.py backend/tests/test_index_api.py -q
python.exe -m pytest -p scripts.offline_guard backend/tests -q
python.exe -m ruff check backend ops evals scripts
python.exe -m pip check
```

本轮复用项目已经批准的 MySQL 持久化/索引代际/检索范围方案；正式企业部署的组织配额、监控与备份仍沿用原部署门槛。本机模型部署与真实语义评测不包含在本步执行操作中。
