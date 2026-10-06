# 文档索引持久化与后端接入

## 本轮完成的代码

1. **MySQL 任务和清单**：迁移 `0012_durable_indexes` 新增 `document_index_jobs`、`index_manifests`，并保存 Run 发起人的身份摘要。解析成功和人工修正复制资料，在同一 SQL 事务里登记索引任务。已有解析资料可通过 POST 接口补登记。
2. **异步索引 worker**：领取、心跳续租、失败退避、最多三次自动尝试、显式重试。每次领取使用新的 generation；逐批从 SQL 验证原文，再调用 Embedding、写入 Milvus、Strong 回读，最后按租约、fence、Case 版本提交 ready。
3. **删除和对账**：Case 删除立即阻止原文使用，任务失效。清理 locator 独立于原文和 snapshot 保存。每次只删除指定文档的指定 generation；失败重试，成功后每 300 秒再次对账，处理迟到 RPC 写入。资料升版遗留的 indexing 清单由 `reconcile_stale` 清理；历史 ready 清单仍按原有历史快照策略保留。
4. **Agent 与 API 接入**：生产 Run 支持从 SQL ready 清单构建融合检索器；没有就绪索引或服务不可用时，显式退回 BM25。每次原文读取、检索、模型调用前后检查当前 Run/资料范围与成员权限；最终答复提交事务再次检查成员权限。身份只保存摘要，不保存用户 token。

Ollama 部署由用户安排的另一个 AI 处理。本轮未安装、下载、启动或探测 Ollama/BGE，未调用 DeepSeek，未更改 `.env`，未修改前端。

## API

接口沿用现有 OIDC 项目成员授权：GET 需读取权限，POST 需写入权限。参数中的项目范围由服务器授权得到；不接受客户端 Milvus filter、collection 或 generation。

| 操作 | 路径 / 请求 | 行为 |
|---|---|---|
| 查看任务 | `GET /api/v1/cases/{case_id}/snapshots/{snapshot_id}/indexes` | 返回每份资料的解析状态、索引状态、尝试次数、就绪数量和固定错误码 |
| 登记或重试 | `POST` 同一路径，`{"expected_revision": 1, "retry_failed": false}` | 202；登记当前已解析文档。`retry_failed: true` 重置失败任务。已有同目标任务幂等保留 |

版本过期返回 409，不存在或无权访问返回 404，删除中的 Case 拒绝索引操作。未解析的资料显示 `awaiting_parse`；解析完自动登记。`worker_configured` 只说明配置开关与目标合法，不代表模型服务可用或索引已经完成。

## 如何启用（本轮不执行）

应用默认 `DOCUCHECK_VECTOR_RETRIEVAL_ENABLED=0`，不因上传资料而访问 Embedding。BGE 部署和模型实际 digest 确认后，在**实际执行索引和 Run 的进程环境**中设置：

```text
DOCUCHECK_VECTOR_RETRIEVAL_ENABLED=1
DOCUCHECK_INDEX_EMBEDDING_REVISION=ollama-bge-m3-<实际64位十六进制digest>-normalized-v1
```

revision 必须来自已确认的模型元信息，不能直接填写 `bge-m3`、模型别名或本轮测试的合成 digest。变化后的模型使用独立 collection，已有 ready 文档通过 POST 重新排队。配置错误不推断模型，也不会自动下载。

宿主 Conda 环境已有 Milvus SDK。独立索引 worker 命令如下；默认 preview 不连接数据库、Embedding 或 Milvus：

```powershell
python.exe -m backend.app.index_worker
# 明确开始工作，会调用配置的服务；模型部署完成前不要执行：
python.exe -m backend.app.index_worker --once
python.exe -m backend.app.index_worker --poll
# 清理不需要 Embedding，仍会连接 SQL/Milvus：
python.exe -m backend.app.index_worker --poll --cleanup-only
```

退出循环使用 Ctrl+C。进程中断后的任务由租约重新领取。清理循环可在关闭向量检索时继续运行。

**当前运行服务边界**：本机 MySQL 已升级至 `0012_durable_indexes (head)`。未重启正在运行的 API/Celery，也未重建 Docker worker；新接口和新的 Run 读取保护需要相关进程加载本轮源码。现有 Docker worker 的默认依赖锁不含 pymilvus，且容器不能把自身 `127.0.0.1` 当作宿主 Ollama。正式启用网页融合检索前，还需安排 worker 的 SDK/宿主服务连接、环境变量传递及镜像同步，并做真实端到端验收。不可仅在 API 进程设置开关就声称网页 Agent 已启用。

## 数据与安全边界

- MySQL 保存原文证据、授权和有效索引清单；Milvus 保存向量和来源 locator，不保存原文正文。
- `original-chunks-v2` 的 chunk ID、指纹和服务器查询过滤均绑定 generation。SQL 未提交 ready 的向量不会进入当前检索；旧 worker 的迟到写入不会冒充新任务。
- 使用 Case → job → manifest 的锁顺序；网络调用期间不持有 SQL 锁。InnoDB 的队列领取采用 `FOR UPDATE SKIP LOCKED`，参考 [MySQL 官方锁定读取文档](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)。
- [Milvus Strong 一致性](https://milvus.io/docs/consistency.md)用于写后回读；SQL 和 Milvus 不构成跨库原子事务，因此依靠 ready 清单、版本过滤和可重试清理实现可恢复流程。
- 已删除清单保留最小 locator 并继续定期清理，防止迟到请求产生孤立向量；本轮没有实现清理记录的长期归档/保留期配置。
- 检索 top-k 不是资料完整性证明。全量抽取、证据引用及业务规则仍按已有 Agent 约束执行。
- 单份索引验证成功不代表整套 Agent 后端改造完成：聊天路由、记忆机制增强和动态审查指标继续按已批准方案推进。

## 验证记录

- 先写失败测试，再实现持久化登记、重领、任务处理、API 与权限绑定。
- 首轮完整离线回归：872 passed / 26 skipped，离线网络尝试 0；后续针对审查发现的删除、权限提交与旧索引对账缺口补了回归。
- 真实 MySQL：`test_index_repository_mysql.py`、`test_document_ingestion_mysql.py`、`test_case_cleanup_mysql.py`、`test_persisted_agent_run_mysql.py` 合计 **4 passed**。包含并发只领取一次、旧 claim 拒绝、source/snapshot 删除后 locator 保留。
- 真实 Milvus：`test_hybrid_milvus_live.py` **1 passed**，使用合成向量验证新 schema、Strong 回读、项目/Case/快照/供应商/generation 隔离以及精确清理。临时 collection 已清理。
- 独立审查及复查：18 项定向测试，网络请求 0；带索引和 actor 幂等记录的 Case 完整清理另经启用外键的 SQLite 复现通过。
- 新增完整 SQL 上传→解析登记→索引 ready→Agent 检索→原文引用→答复落库测试，使用假 Embedding 与脚本化 Provider。真实 BGE 语义效果不在本轮验证结论之内。
- 最终完整离线报告：`data/verification/durable-indexing-final-offline.json`，**874 passed / 27 skipped，214.94 秒，网络尝试 0**；Ruff、`pip check`、`git diff --check` 均通过。27 项跳过的外部集成项不计入离线通过数，真实服务的 4+1 项验证单独列出。
