# 中文原文混合检索交付记录

## 实现范围

本轮属于聊天式采购助手改造的后端大步骤 2。实现源文本分块、本机 BGE-M3 接口、显式 Milvus schema、BM25Plus 与向量两路融合、Agent 检索器注入，以及可执行的本地语义检索 CLI。

### 1. 原文与来源

`backend/app/evidence/chunks.py` 将 PDF 解析段落和 Excel 行转换为版本化块。Excel 可以附带同文档、同 sheet 首行的原文上下文；不推断首行一定是表头。每个来源片段保存 evidence_id 和原文字符起止偏移，换行拼接有明确顺序，能够回读验证，不将模型补写写入源索引。

初值为 1600 UTF-8 字节与 180 字节重叠，是保守输入预算，不是精确的 300–600 token。Agent 工具命中文本仍限 800 字节，长原文通过既有 read_source 翻页继续读取。项目、Case、快照、文件哈希、parser、供应商/型号绑定、分块策略、模型摘要和维度都影响稳定 chunk_id。未确认品目保留 item_ids=[]，当前不自动做跨品目匹配。

### 2. Embedding

`backend/app/vector_store/embedding.py` 使用本机 Ollama HTTP 接口，单批最多 8 段；不自动下载、不跳转到远端、不使用代理环境。通过 /api/tags 获取完整模型摘要并在每批请求前后核对；摘要改变拒绝查询旧索引。/api/embed 明确 truncate=false，校验向量数量、1024 维、有限非零值并归一化。超时、服务异常和格式错误返回固定错误码，不输出采购原文或服务器响应。

这是检索的向量模型，与 DeepSeek 的聊天/工具 Provider 分开。真实中文语义召回必须等模型安装完成后实测，合成向量测试不证明语义质量。

### 3. Milvus 与混合检索

`backend/app/vector_store/store.py` 使用 VARCHAR64 主键、FLOAT_VECTOR、显式项目/Case/快照/文档/供应商/角色标量、指纹及 JSON 来源映射；动态字段关闭，FLAT/COSINE，关键过滤字段使用 INVERTED，读取显式 Strong。原文不存到 Milvus，命中只提供 chunk_id 和指纹；融合后从仓库重新读取原文。

`backend/app/evidence/hybrid.py` 在两路召回前过滤当前快照及供应商，保留共享 requirement。各最多 20 候选，RRF k=60，按块 ID 去重、稳定排序；不直接相加不同量纲的原始分数。任意未知块或错误指纹拒绝披露。服务不可用或尚未 ready 时，BM25 降级原因出现在命中及工具顶层 retrieval 诊断，空命中也有诊断。top-k 始终 complete_coverage=false。

`build_index` 批量 upsert 并 Strong 回读核对预期主键与指纹之后才能签发进程内 IndexReceipt。调用方可以在索引批次前后、查询网络等待前后、原文读取前后绑定权限/删除检查回调。Agent 在工具操作及最终来源答复前后也调用该回调，防止取回来源后权限失效仍提交旧答案。

### 4. Agent 接入

ToolExecutor/AgentSession 允许显式传入 HybridRetriever，并要求 repository 是同一对象。实际 LangGraph 回归演练了 search_evidence → submit_answer 的带来源路径。进入 ask 时先检查权限，包含未知供应商等早退分支；每次 Provider 调用前也检查，避免缓存原文在撤权后继续发送。完整审查仍使用全量抽取和规则判定；既有调用默认维持 BM25Plus。

## 当前尚未启用的生产接入

IndexReceipt 是本进程的读回验证，不能当成持久化 SQL manifest 或身份授权。当前网页 API/worker 没有自动调用新检索器；默认无操作的 revalidate 回调只适用于固定模拟包 CLI。正式 API 接入必须在下一项补齐：

- MySQL index_manifests/document_index_jobs、唯一版本约束、就绪计数与内容指纹。
- 原文解析落库与索引任务同事务登记；异步 worker 领取租约、重试、幂等及迟到写入栅栏。
- 从当前 SQL 权限与未删除任务生成范围、等待后重查；删除索引及跨库失败对账。
- 显式品目绑定与 item_ids 过滤；独立中文检索金标准和容量测试。

此交付没有实现新的聊天消息持久化、摘要/长期记忆或动态审查指标，也没有修改前端；它们仍在后端大步骤 2 和前端大步骤 3 的计划中。

## 用户安装步骤

1. 从 [Ollama Windows](https://ollama.com/download/windows) 安装，重新打开 PowerShell。
2. 执行 `ollama pull bge-m3`，模型约 1.2GB；然后 `ollama list` 确认有 bge-m3:latest。
3. 若 Ollama 没有自动启动，在独立终端执行 `ollama serve` 并保留窗口。已有服务时无需再启动一个。
4. 已激活 docucheck 环境，在项目根目录运行：

```powershell
python.exe -m backend.app.retrieval_demo
python.exe -m backend.app.retrieval_demo --send --question "什么时候能送到？"
```

第一条只预览已哈希核验的模拟包，不联网；第二条把指定包的模拟采购原文发送给本机 Ollama，向量写入本机已隔离的 Milvus 临时集合，检索后删除此集合，不调用 DeepSeek。默认读取 examples/dev-pack-001/vendor-a，可以用 --case-dir/--supplier 切换其他已验证模拟包。

输出包含模型实际 revision、已索引块数、索引/查询耗时、BM25 基线、融合命中、通道/rank、降级原因、Embedding 请求数及清理结果。Embedding 请求数与聊天大模型调用数分开记录。每次 CLI 演示重建临时索引，不代表生产 API 会每轮重新嵌入资料。

## 校验记录

当前定向验证：新组件 46 项离线测试（来源/Embedding/schema/融合/CLI/权限失效），加既有 Agent graph 共 **68 passed**，网络尝试 0。真实 Milvus 集成 **1 passed**，覆盖显式 schema 复用、重复 upsert、项目/Case/快照/供应商过滤、Strong 可见性、删除及临时集合清理。验收中修复了 schema description 写入位置和 SDK 自定义 VARCHAR 主键返回字段名两处实际兼容问题。

只读审查独立复现并验证修复原文回读期间撤权、检索后/下一次 Provider 前撤权、未知供应商早退元数据披露等边界。表格首行上下文改为预计算，扫描次数回归通过；同机合成样本 4000/8000 单元格的纯分块耗时从 19.078/78.298 秒降为 0.043/0.088 秒。该数字不含解析、Embedding、数据库或模型调用，不代表整个业务链路延迟。

最终当前源码全量离线回归 **855 passed、26 skipped，184.15 秒，offline_network_attempts=0**；26 项为 opt-in 服务测试（包括新增 Milvus 集成）。本轮另显式运行新增真实 Milvus 集成 1 项通过。Ruff、pip check、Git diff check 和 13 个新文件的尾随空白检查通过；默认 CLI preview 读取 29 个来源块，网络/Embedding/聊天模型调用均为 0。Milvus healthy、无 OOM、重启数 0，观测约 150MiB / 2GiB。

真实 BGE-M3 语义对照目前未执行；不能报告 RAG 质量分数或完成所有后端改造。本轮没有调用 DeepSeek，没有安装 Ollama/下载模型，没有修改前端，也没有提交或覆盖前轮工作。下一项按计划接 MySQL 索引任务与 worker，不直接用进程内 receipt 开启生产 API。

## 官方依据

- [Ollama Embedding API](https://docs.ollama.com/api/embed)：禁止静默截断、有限批量。
- [Ollama 模型元信息](https://docs.ollama.com/api/tags)：使用真实模型摘要。
- [BGE-M3 模型](https://huggingface.co/BAAI/bge-m3) 与 [Ollama 模型包](https://ollama.com/library/bge-m3)：中文/多语支持、1024 维及安装信息。
- [Milvus 2.6 schema](https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Collections/create_schema.md)。SDK 具体响应字段另经本机版本实测。

分块预算、RRF 参数、资源消耗和检索准确率需要采购问题集评测，不由文档或框架热度保证。
