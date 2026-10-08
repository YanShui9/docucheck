# BGE/Milvus 实连复验与故障降级记录

日期：2026-10-08。基线：`a74aac7`。**本步部分完成：BGE 已恢复，Milvus 仍无法启动，向量检索整项不勾选。** 本步没有修改应用代码或前端，仅补充两项故障边界回归及验收记录。

## 服务恢复与失败

- 复用 `E:/Ollama/ollama.exe`、已有 `E:/Ollama/models`，隐藏后台启动，PID 14156，监听 **127.0.0.1:11634**。没有重新下载模型或开放公网监听。Ollama 0.31.2，BGE-M3 F16/1024维，模型 digest 与配置一致：`7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`。
- 对既有 `docucheck-vector-milvus-1` 执行两次 `docker start`；两次均退出134，OOM标记false，自动重启计数0。镜像仍为 `docucheck-milvus:2.6.25`，没有构建、替换或清空 `docucheck-vector_milvus_data`。
- 两次日志均显示启动期 etcd 首次选主期间，读取 `by-dev/meta/session/id` 返回 `etcdserver: leader changed`，Milvus `sessionutil.(*Session).Init` 随后panic。第二次日志读耗时约1.88秒。这确定了直接失败路径，**尚不能确定底层调度、磁盘或启动时序的具体触发原因**。没有凭此认定数据卷损坏，也没有修改选举参数、重置凭据或无限重启。
- 上游 [etcd issue #21335](https://github.com/etcd-io/etcd/issues/21335)报告过 standalone Milvus 与 etcd 3.5.23 的类似panic；[Milvus v2.6.25会话源码](https://github.com/milvus-io/milvus/blob/v2.6.25/internal/util/sessionutil/session_util.go)可核对初始化错误路径。另一个[运行期故障issue](https://github.com/milvus-io/milvus/issues/51659)涉及watcher，与本次启动期堆栈不同，不能据此宣称已找到修复版本。

## 本次实测通过的范围

1. Ollama `/api/version` 和 `/api/tags` 返回200，revision精确匹配 `.env` 的索引版本。
2. 使用项目 `OllamaEmbedding` 对两句合成采购文字生成向量：两条均1024维、有限且归一化，范数1.0，当前首批观测4.406秒；不是性能基准。
3. 运行中的 `docucheck-indexer-1` 访问 `http://host.docker.internal:11634/api/version` 返回200。此项只验证容器到宿主Ollama的连通性，不代表indexer已经成功写入索引。
4. 本地诊断用经hash校验的 `examples/dev-pack-001`、真实BGE查询向量及真实Milvus SDK连接。查询“内存”“存照片和文件的硬盘有多大？”均因Milvus不可用，明确返回 `bm25 / vector_service_unavailable / dense_candidates=0 / complete_coverage=false`。分别找回16GB相关原文和 `标称 SSD 1 TB`，引用回读及供应商约束通过。
5. 上一项诊断为进入查询分支构造了**合成的进程内receipt**；没有写向量或建立SQL manifest。因此只证明真实连接失败时的组件降级行为，不能证明完整持久化RAG、SQL权限、网页答案或索引就绪。硬盘查询命中来自既有词法别名规则，不能算作语义检索成功。

诊断适配器共 **3次本机 `/api/embed` 请求**（首批1次、两个查询各1次），Milvus SDK连接尝试2次，向量写入0，DeepSeek请求0。运行中的indexer可能自行轮询模型元数据；这些数量只统计本诊断适配器，不作为全机Ollama请求总数。

## 回归与运行边界

- 新增 `test_ready_index_dependency_outage_degrades_and_keeps_source_guard` 两个参数场景：SQL ready manifest下，Embedding工厂或Milvus工厂异常，均返回显式词法降级；已创建Embedding被关闭；后续SQL原文被篡改时继续拒绝披露。这是SQLite及替身依赖的离线边界测试，不冒充MySQL/Milvus实连。
- 最终七个相关测试模块 **92 passed / 网络尝试0**，10.00秒；`pip check`、`ruff check backend evals scripts`通过。没有应用代码改动，本步未重复全量后端或前端构建，不把上步全量1269项成绩记作本步新成绩。
- 因Milvus启动失败，`test_milvus_live.py`、`test_hybrid_milvus_live.py`、`test_persisted_bge_live.py`的成功路径实连测试**未运行**；不记为通过或跳过成功。不创建或删除任何Milvus集合。
- API健康live/ready均200、匿名 `/api/v1/me` 返回401。这里API ready不意味着可选向量服务ready。既有Run `d2afc28c48d547688e72d1864947cb5f` 仍为waiting_user。
- 当前MySQL索引任务观测5条failed，缺少本步之前的状态快照，不能归因本次恢复操作。没有对这些任务执行重试或伪造ready；服务恢复后需要按资料版本、权限和generation核对，再选择性显式重试。
- API/前端及其他Docker容器未被本步重启；用户旧 `milvus-standalone` 与独立验证栈未操作。上步worker相差1个Python文件的部署差异仍保留，不能声称已统一部署。

## 复核证据与下一项

[脱敏JSON证据](repair-rag-reverification-evidence-2026-10-08.json)记录基线、服务结果、诊断、最终测试输出及原始文件SHA-256。原始日志、诊断脚本与结果在被忽略的 `data/verification/rag-reverification-live-2026-10-08/`；运行中的Ollama日志会继续变化，不纳入固定hash证据。凭据及环境文件不进入提交。

下一项先解决 **Milvus持久化实例的启动故障**，保留现有卷和权限；恢复后再执行已有三组实连测试，核对既有失败索引并进行独立合成资料的持久化语义检索。不能通过删除数据卷、放开权限、调高重试次数或改为词法命中来关闭本项。此步骤尚未完成整个实连验收，也不提供多品类准确率、无答案拒答、生产容量或高可用证明。

本步只恢复了Ollama服务；如需撤销本步后台启动，先核对14156仍为 `E:/Ollama/ollama.exe` 且监听11634，再停止该进程。测试和文档可按本步Git提交回退，不涉及数据库迁移或用户资料回写。
