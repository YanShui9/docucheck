# 中文原文混合检索执行计划

> 本轮在现有工作区按 executing-plans 执行，先测试再实现；不创建额外分支或提交。

**Goal:** 给既有 Agent 的 search_evidence 接入可注入的、来源可核验的 Milvus + BM25Plus 检索器。

**Architecture:** 从已授权且固定快照的 EvidenceRepository 生成有字符偏移的原文块。本机 Ollama BGE-M3 提供向量，集合按模型摘要、维度、分块版本隔离；Milvus 命中只提供定位，文本从仓库读回。两路在供应商过滤后采用 RRF 融合。

**Tech Stack:** 现有 Python、httpx、pymilvus 2.6.17、Milvus 2.6.25、BM25Plus、LangGraph。

## 本轮范围和交付边界

当前属于已批准的后端大步骤 2 的检索交付。完成可执行的检索组件、CLI 和 Agent 注入，保持既有 API 默认检索行为。生产 API 的自动索引启用依赖下一项：MySQL 持久化 index_manifests/document_index_jobs、解析事务登记、worker 租约/删除栅栏/权限重读。此次进程内的验证句柄不能替代这些机制。

不修改前端。完整审查仍走全量原文扫描，top-k 不证明资料缺失。模型由用户安装/下载，本轮不自动安装推理运行程序或下载模型。

## A. 来源分块和本机 Embedding

Files: `backend/app/evidence/chunks.py`、`backend/app/vector_store/embedding.py`；tests: `backend/tests/test_source_chunks.py`、`backend/tests/test_ollama_embedding.py`。

- [x] 测试先失败：字符偏移可恢复原文、表格行不拆散来源、跨文档隔离、Unicode 上限、稳定主键及绑定/模型变化使主键变化。
- [x] 实现保守 UTF-8 字节预算（1600 字节，180 字节重叠；并非精确 tokenizer 计数），保留完整偏移映射及未分配品目信息，不添加模型补写。
- [x] Ollama 配置仅允许本机地址；GET /api/tags 获取并绑定实际 digest，POST /api/embed 使用 truncate=false、小批量和有限超时；校验数量、1024 维、有限非零向量，模型更换中止。
- [x] 运行上述测试，失败响应不得暴露请求文本、URL 凭据或外部响应内容。

## B. Milvus、融合和 Agent 接入

Files: `backend/app/vector_store/store.py`、`backend/app/evidence/hybrid.py`、`backend/app/agent/tools.py`、`backend/app/agent/graph.py`。

- [x] 测试先失败：服务端 scope 表达式转义、不同项目/快照/供应商命中拒绝、Strong 可见性、错误模型维度、重复写入幂等、就绪验证失败不能检索。
- [x] 明确 schema，VARCHAR64 主键、FLOAT_VECTOR、标量范围和 JSON 来源；FLAT/COSINE/Strong，不接受用户输入的原始 filter。
- [x] 对同一有权限的原文块执行 BM25Plus 和 dense，各最多 20 候选；RRF k=60，去重、稳定排序，保留通道与降级原因。向量服务不可用显式降级；伪造或越界命中拒绝。
- [x] AgentSession/ToolExecutor 接受可选检索器，验证 repository 对象一致；旧调用保持兼容。测试工具实际读取融合结果，原文读取和全量抽取保持既有约束。

## C. 可执行验证和文档

Files: `backend/app/retrieval_demo.py`、`backend/tests/test_hybrid_retrieval.py`、`backend/tests/test_hybrid_milvus_live.py`、`docs/hybrid-retrieval-2026-10-03.md`、`.env.example`、根入口文档。

- [x] 默认 preview 不联网；显式运行仅使用指定的哈希核验模拟包、本机 Embedding、隔离临时集合，完成后清理。
- [x] 真 Milvus 集成测试使用合成向量验证适配器范围/读写/删除，不把合成向量测试称为真实语义召回。
- [ ] 用户准备好 Ollama 后，运行模拟采购原文的语义检索对照；否则保留明确未验收项。
- [x] focused tests、完整 offline regression、Ruff、pip check、git diff check；记录实际结果与剩余生产接入条件。

本轮最终：新组件 46 项，加既有 Agent graph 定向 68 passed；完整离线 855 passed/26 skipped，网络尝试 0；真实 Milvus 新适配器 1 passed。只读复审的问题已修复，详见交付记录。只有模型安装后的真实语义对照仍未勾选；正式 API 的持久化任务、权限和删除栅栏在本轮范围以外的下一项。

```powershell
python.exe -m pytest -p scripts.offline_guard backend/tests/test_source_chunks.py backend/tests/test_ollama_embedding.py backend/tests/test_hybrid_retrieval.py -q
python.exe -m pytest -p scripts.offline_guard backend/tests -q
python.exe -m ruff check backend evals scripts
python.exe -m pip check
```

## 来源

- https://docs.ollama.com/api/embed
- https://docs.ollama.com/api/tags
- https://ollama.com/library/bge-m3
- https://huggingface.co/BAAI/bge-m3
- https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Collections/create_schema.md

RRF k=60 与分块字节数是待评测的项目参数，不是采购领域准确率承诺。
