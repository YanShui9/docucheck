# 检索验收交接复核与补验

## 接收外部验收

已阅读 `ollama-milvus-live-2026-10-04.md`，并读取 metadata、retrieval、storage-diagnostic、final-state 四份原始 JSON。记录中的 1024 维、实际模型 revision、8 题、临时集合删除及结束时集合为空互相对应。该报告是另一 AI 的开发探针验收，不是独立封存集或正式 RAG 准确率。

外部记录的模型 revision：`ollama-bge-m3-7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab-normalized-v1`。复核时除本轮已修改的 retrieval.py 外，记录中的 CLI、Embedding、Milvus store、hybrid、repository 和开发包 manifest 源码指纹均匹配原验收快照。后续聊天实现对 repository 的显式空范围扩展需用本轮回归另行验证，不能继承外部源码一致性结论。

## 修复原题漏召回

仅新增词法 alias `硬盘 → 存储`。Embedding 仍使用原问题；未把 RAM/内存映射到存储，未改变来源角色、权限、供应商过滤和完整覆盖判断。

- 红灯：真实开发包原题 BM25 没有命中，固定 SSD dense 第 8 的离线融合没有进入 top-6。
- 绿灯：检索回归 32 项通过；独立只读复审再次运行 32 项，并在独立进程临时移除 alias 复现原题失败，没有改回应用文件。
- 真实重测：`retrieval-storage-after-alias-2026-10-04.json` 为 completed；BM25 第 1、混合第 2 命中“标称 SSD 1 TB”。29 个索引块，索引 11.547 秒，查询 1.326 秒，Embedding 请求 5，聊天模型调用 0，临时集合已删除。

本次重测是一个开发问题、一次运行，不能推导所有品类召回效果、生产延迟或 SLA。

## 持久索引补验

新增 opt-in `test_persisted_bge_live.py`，真实本机 BGE-M3 与 Milvus通过以下路径验证：上传全新合成 XLSX → 原文 SQL 保存 → 索引任务/租约 → 向量化及 Strong 回读 → SQL manifest ready → 独立客户端从 manifest 构造当前 Run 检索 → SQL 原文回读与单元格核对。**1 passed**。

该测试使用隔离的 SQLite 测试数据库和唯一临时 Milvus 集合，未读取或抢占真实业务的索引队列。它证明应用的持久索引检索路径与真实向量服务相接；真实 MySQL 与容器运行验收单独记录，不把 SQLite 称为 MySQL。

无答案问题仍可能返回近邻，`complete_coverage=false` 保持。聊天答复还需验证来源与相关性，不能依据原文近邻直接形成完整审查判定。

## 服务状态变化

开始补验时 Docker Desktop 被用户手动暂停。用户恢复后，既有 Milvus 容器因内部 session lease 过期退出，`OOMKilled=false`；日志中的租约过期是本次退出的证据，未推断其他原因。告知用户后复用原容器与 `docucheck-vector_milvus_data` 卷启动一次，实测 running/healthy。旧卷和外部验收产物未删除。

Docker worker 到 `http://host.docker.internal:11434/api/version` 实测 HTTP 200。应用配置仅增加这一明确的 Docker Desktop 宿主桥地址，仍拒绝任意远程 HTTP 主机、凭据与路径 URL；本机 Ollama 的监听地址保持原配置。容器部署的最终 SDK、索引 worker、调用和权限证据见后端统一交付记录。
