# 可交给另一个 AI：本机 Embedding 与 Milvus 验收

这是用户可复制给另一个 AI 的任务说明；主线程正在实现动态审查清单，请勿编辑同一批业务文件。

## 任务

在 `E:\PycharmProjects\docucheck` 阅读 AGENTS.md、CLAUDE.md、README.md 后，验证已下载的 Ollama BGE-M3 能通过项目适配器生成向量，并完成 Milvus 中的真实中文混合检索对照。使用 Conda `docucheck`，不调用 DeepSeek 或其他收费聊天模型。

2026-10-04 本轮现场观察：`http://127.0.0.1:11434/api/tags` 未连通，Docker 当前运行列表没有 Milvus 容器。此前部署状态可能变化，先核对实际安装位置、端口和现有容器，不重复下载模型或创建新数据卷。需要启动 Docker 服务或进程时先告诉用户，确认配置只监听本机。

## 可以读取与执行

- `backend/app/vector_store/embedding.py`、`config.py`、`store.py`、`target.py`。
- `backend/app/retrieval_demo.py`、`backend/app/index_worker.py`、`backend/app/db/persisted_retrieval.py`、`index_worker.py`。
- `docs/hybrid-retrieval-2026-10-03.md`、`docs/durable-indexing-2026-10-03.md`、`docs/milvus-live-acceptance-2026-10-03.md`。
- 原有 `compose.milvus.yaml` 与 `compose.milvus-smoke.yaml`、现有配置/凭据；不要把秘密写进报告或发给主线程。
- 先执行只读 metadata/健康检查，复用用户已下载的模型。模型必须由 `/api/tags` 核对 digest，向量实际为 1024 维、有限、非零；不只报告“ollama list 看到了”。
- 可执行 `python.exe -m backend.app.retrieval_demo --send`；该命令只使用开发合成包和独立临时集合，结束清理集合。另选至少五个中文改写问题与 BM25 基线比较，保留 source IDs/定位、模型 revision、冷/热查询耗时和错误。
- 旧 API/Celery 不要重建、重启或启用全局向量开关；生产资料自动索引联调由主线程统一安排。

## 写入范围

只新增 `docs/ollama-milvus-live-2026-10-04.md` 和 `data/verification/ollama-milvus-*.json`。若确有缺陷，记录文件/行号、复现及建议，把修复交给主线程。不要编辑 `.env`、依赖锁、Compose、backend/app、backend/migrations、已有测试、前端或项目入口文档；不提交 Git。

## 交付结果

1. Ollama 地址、版本、模型名与 digest（不含秘密）、实际向量校验。
2. Milvus 地址/版本、认证是否有效、使用哪个专用库和临时集合是否清理。
3. 至少五个改写问题的 BM25 与混合命中对照、相关原文定位，出现未召回或降级必须保留。
4. 冷启动、索引、冷/热查询耗时，CPU/GPU 实际运行形态；不要以单次成功宣称质量提升或满足生产 SLA。
5. 有阻碍时给出准确的错误码和下一操作，不删除/覆盖旧容器、卷或凭据，也不阻塞主线程的动态指标开发。

次选并行任务是收集包装食品采购需求/订单/报价/规格的合法公开资料并登记来源/hash。只写新样例候选目录和来源报告，gold 与资料隔离；暂不修改业务代码或声称独立人工封存。
