# 聊天式采购助手后端收尾与统一验收

日期：2026-10-05。范围：完成上下文、记忆、混合检索、动态指标和聊天路由的后端交付；前端未修改，仍须先讨论聊天页面的规范与样式。本轮没有提交 Git。

## 1. 已实现的业务流程

1. 无文件也能新建采购任务并聊天。新增 `operation=assistant` 使用既有消息、Run、队列与持久化协议；空资料对应明确 revision 0，不借用其他任务资料。
2. 用户直接描述要求、手工设置指标，或从买方需求/订单提出候选。品类与指标名称无白名单；每次采购独立设置品目、指标和阈值。复杂条件保存为人工复核项。
3. 自然语言增删改返回有限 patch、完整清单和程序重算的差异。未修改项保留稳定 ID；用户修改明确标为 user_defined，保留指令与理由。Agent 不保存或确认清单，须用户明确 PUT 草稿再 POST confirm。
4. 资料问答使用本机 BGE-M3、Milvus 与 BM25Plus/RRF；持久索引按 SQL manifest、资料版本和权限校验。检索后回读原文，摘录必须有真实来源。无直接原文则说明“本次检索未找到”；top-k 不证明全量覆盖。
5. 正式审查复用同一 Run、预算和动态审查子流程，枚举已确认且启用的指标及供应商，执行完整抽取与有限确定性规则。结果、事实、版本、修正及导出可追溯。普通聊天不会产生正式 checks。
6. MySQL 保存消息、运行状态、带来源的 task memory 和摘要；按上下文预算恢复同会话前文。偏好仅支持有限键、明确确认、到期与删除。撤权、取消、资料/清单升版使旧当前结果失效，历史按原版本保留。

接口、示例及启动步骤见 [后端 API 交接](backend-assistant-api-2026-10-04.md)。主要新增代码：`agent/assistant.py`、`criteria/changes.py`、`db/assistant_repository.py`、`db/assistant_executor.py`、`0016_assistant_runs.py`。

## 2. 审查与联调发现的修复

| 问题 | 修复与验证 |
| --- | --- |
| 外部检索交接中的“硬盘”原题召回失败 | 增加“硬盘→存储”词法别名，保留 SSD；原题实际 BM25 rank 1 / hybrid rank 2。原始报告与失败保留。 |
| source_reads 可超过持久化 schema 的 96 项上限 | 运行时与 schema 共享限额，读前/读后检查，合法 partial 仍可回读；入库前先验证整个 payload，非法结果事务回滚。 |
| 无文件文字要求误用买方文件候选工具 | 无 requirement 文档时剔除 propose_checklist；明确文字要求使用 propose_changes。隐藏工具即使被 Provider 返回也拒绝，不进入正式子流程。 |
| 中文指标“苹果数量”与原文“苹果 数量25箱”不匹配 | 精确匹配优先，仅无精确命中时为全中文标题兼容半角/全角空格；不跨分句，不修改数值、单位或 quote。六项回归保留历史双数值的歧义/待人工确认。 |
| 重试 formal partial 后普通聊天可能残留动态载荷 | 新终态清理旧动态载荷，聊天不能提升旧事实为正式结果。 |
| 真实 worker 启动缺少 jwt | worker lock 加入项目使用的 PyJWT；Docker build 增加真实入口 import gate，再实际重建/启动，避免只做 pip check 而漏 import。 |
| 本机三包与锁不一致 | 对齐 langsmith 0.14.3、openai 3.23.0、regex 2026.9.29，依赖检查通过。 |
| 索引清理测试使用固定时间、生产实际时间已超过它 | 仅冻结测试中的删除时钟，保留生产时钟。 |
| 实连测试断言可能打印 Milvus token、集合创建失败留下集合、后台 cleaner 抢先处理 | 改为不带 secret 的布尔断言；UUID 专属集合预先声明归属并 finally 清理；只等待本测试 Case，不抢占其他任务。 |

完成独立规格与质量审查；最后的路由及中文空格兼容 delta 无剩余 P1/P2。该结论限于审查范围，不等同所有生产风险已穷尽。

## 3. 验收证据

### 离线与数据库

最终全量 **1178 passed / 37 skipped / 0 failed**，离线网络尝试 **0**，255.04秒；pip check、Ruff 均通过。报告为 `data/verification/backend-completion-post-layout-final-2026-10-05.json`。37项跳过的是显式开关控制的外部服务验收，没有把跳过计为通过；必要的 MySQL、BGE/Milvus 与真实队列验收单独记录如下。

- 中文标题修复前三组定向测试 136 passed / 网络 0；最终兼容补丁指定六项 6 passed / 网络 0。
- 多品类新增六项端到端脚本 Provider 回归：苹果净重、服装含棉量、显示器亮度、大米包装重量、陶瓷杯耐温，以及多品目同名/人工项/累积部分确认/历史失效。真实 XLSX 上传解析、候选保存确认、两供应商结果、来源与 JSON/XLSX 导出均验证。这些是业务链路回归，不是真实模型多品类准确率成绩。
- 2026-10-05 实连 `test_assistant_mysql.py` 与 `test_persisted_bge_live.py`：**2 passed**，见 `data/verification/backend-integrations-final-2026-10-05.json`。前者用真实 MySQL 验证 revision 0、幂等/重试/重新打开数据库/候选确认与历史；后者用隔离 SQLite manifest + 真实 BGE/Milvus 验证混合检索、原文及无答案范围。后者不称为 MySQL 实连。
- 上一轮相关 MySQL 组合 10 passed、真实持久检索 1 passed 的记录保留为阶段历史。
- 实际 Alembic current：**0016_assistant_runs (head)**。

### 真实队列、模型与检索

`data/verification/backend-queue-live-metric-layout-2026-10-05.json` 实际 passed=true、cleanup=true，验证六项：空资料聊天不产生正式结论、文字要求候选、容器 indexer ready manifest、明确保存确认、真实 BGE/Milvus 问答、同 Run 正式审查及导出。

该次使用真实 MySQL、outbox、Redis、Celery、DeepSeek 与本机 BGE/Milvus；API 提交/读取采用进程内 LocalAuthenticator TestClient。它不是实际 HTTP OIDC 已登录用户全流程验收。

四个 Run 全部 completed，共 **7 次真实聊天模型请求**，已知 input **22720** / output **813** tokens，unknown_usage_calls=0。原文是新建的合成 XLSX“苹果 数量25箱”，已确认要求为至少20箱，正式结果为“满足”，引用为采购资料!A1，版本与确认版本一致。问答 retrieval_mode=hybrid、无降级、complete_coverage=false。测试结束清理自己的 Case、文件与索引 manifest；没有删除用户集合或其他 Case。

收尾队列探针包含失败的请求累计 **16 次**；实际账单金额未知。失败记录没有重写为成功：

- `backend-queue-live-2026-10-04.json`：worker 缺 jwt，0 请求，清理成功。
- `backend-queue-live-post-packaging-2026-10-04.json`：候选路由失败，2 请求，清理成功。
- `backend-queue-live-2026-10-05.json`：前五项通过，正式审查 coverage_incomplete，7 请求，清理成功。
- 最后成功记录为 `backend-queue-live-metric-layout-2026-10-05.json`，7 请求。

离线首轮时钟测试失败及中断的依赖对齐验证也保留。`backend-completion-final-2026-10-05.json` 的 1172 passed / 37 skipped / 网络0 对应空格修复前，不能替代最后源码的全量 gate。

## 4. 当前运行服务

已重建并更新 worker、indexer、cleaner；MySQL/Milvus healthy。实际 worker/indexer/cleaner RestartCount=0。构建采用 Python 3.12-slim，实际 base digest 为 `sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d`；Dockerfile 的 base tag 尚非永久 digest 固定，不宣称未来重建完全逐字节可复现。

`data/verification/backend-runtime-final-2026-10-05.json` 记录实际镜像 ID、源码 hash、HTTP 状态及依赖锁一致性。worker 内 assistant.py、criteria/validation.py 与当前宿主源码逐字节一致；worker 76包、vector 17包、dev 102包均匹配当前 Conda 环境。

API 使用本机 OIDC 配置，`/health/live` 与 `/health/ready` 为200，未登录 `/api/v1/me` 为401。API 已读 `.env` 并启用向量配置。Compose indexer healthy 只检查配置，索引可用性以本次真实 ready manifest 和问答验证为依据。

Ollama 当前监听 loopback **11634**，使用现有 `E:/Ollama/models`，未重新下载模型。BGE-M3 digest 为 `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`。本次 Windows 排除范围包含默认11434，启动曾报10013；改用可用端口及参数化 Docker 宿主桥接覆盖项后，容器到 Ollama `/api/version` 实际200。排除端口与10013的关系见 [Microsoft 说明](https://learn.microsoft.com/en-us/troubleshoot/windows-server/networking/error-10013-wsaeacces-is-returned)，监听地址环境变量见 [Ollama FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.mdx)。启动细节见 API 交接。

API、Ollama 目前是本轮受控前台进程，未安装为 Windows 常驻服务；退出或重启电脑后须按交接启动。保留其他独立验证栈及用户既有任务。

## 5. 剩余边界与下一阶段

- 后端流程已实现，旧网页不会自动变成聊天界面。下一阶段先讨论中文聊天页、动态指标候选确认、审查面板与原文布局，再写前端。
- 任意品类指名称/清单可动态变化，不代表任何语言要求都能自动判定。执行规则有限：数值/区间、金额、枚举、资料存在、期限和人工复核；证照真实有效性、复杂条件、未知单位/日历需人工确认。
- 普通 chat 是模型文本，资料摘录验证存在和范围，不保证所有相关性和幻觉问题已穷尽。真实多品类采购资料准确率、压力容量、独立封存集、灾备/高可用及正式企业部署仍需单独验收。
- 上下文机制使用 MySQL 应用持久化，不宣称采用 LangGraph 官方 checkpointer 或全节点任意续跑；摘要是有来源摘录，尚非模型语义摘要。自然语言自动保存个人偏好、模板库、私有/共享会话选择未实现。
- 服务不可用保留降级/未完成；检索命中不能提升为完整正式判断。模型不能自行确认要求、写任意规则代码或绕过权限。
