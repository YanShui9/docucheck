# 聊天式采购助手后端接口交接

本轮只实现后端。现有网页仍是原工作台；聊天主界面、面板展开方式和视觉规范须先与用户讨论。代码和本轮验证见 [收尾报告](backend-completion-2026-10-05.md)。

## 用户流程

1. 新建采购任务及会话，可以先聊天、说明需求，也可以先上传资料。任务没有文件时使用 revision 0 的明确空快照。
2. 用户可直接设置指标、用聊天提出增删改要求，或让助手从买方需求/订单文件整理候选。品类、指标名和阈值由每次采购决定，示例不构成白名单。
3. 候选展示完整清单和差异；用户保存草稿，再明确确认。模型的工具调用不会替用户确认。
4. 上传供应商资料后可询问原文；正式审查按已确认且启用的指标逐项执行，并保留来源和未完成范围。
5. 修改文件会产生新资料快照，旧要求/报告按历史保留；新快照需要重新保存并确认适用清单。旧 Run 不跨资料版本重试；等待中的旧 Run 可以取消后在新版本重新提问。

## 同一个持久化消息入口

`POST /api/v1/conversations/{conversation_id}/messages`

Header：`Idempotency-Key`；OIDC 模式还需正常 Bearer access token。

```json
{
  "content": "采购苹果，数量至少20箱，请整理要求候选",
  "operation": "assistant",
  "mode": "standard",
  "expected_revision": 0
}
```

返回 202 的 Run/attempt 回执。HTTP 只提交事务和 outbox，后台 worker 执行；继续使用原 Run 状态、事件、取消、retry/resume 接口。已有调用不传 operation 时保持 review 语义；旧入口仍不接受 revision 0。

`GET /api/v1/agent-runs/{run_id}/assistant-result` 返回固定基准及资料版本，`result.kind` 为：

| kind | 含义 | 业务结果 |
| --- | --- | --- |
| chat | 一般交流 | 不产生采购事实、checks 或正式导出 |
| evidence | 本次资料检索问答 | 仅经原文核对的摘录、角色/供应商与页码/单元格 |
| changes | 用户要求的变更候选 | 完整 candidate、有限 patch 和程序重算的 changes |
| proposal | 从买方文件提出指标 | 复用动态候选接口及原文/品目验证 |
| review | 正式审查 | 同 Run 接入动态 facts/checks/export，复用确认版本与全量覆盖验证 |

变更候选还可通过 `GET .../{run_id}/proposal` 获取 `definition`、`changes`、`requires_user_confirmation=true`。只有 changes/proposal 可提供候选，聊天及摘录不生成正式采购报告。

## 明确保存与确认

沿用 `PUT /api/v1/cases/{case_id}/snapshots/{snapshot_id}/checklist`：完整 definition 加 `expected_revision`、`expected_checklist_revision` 和 reason；随后 `POST .../checklist/confirm` 加 criterion_ids、两个 revision、reason、`confirmed=true`。两步均须自己的幂等 key。

每项使用稳定 criterion_id/item_id。有限 patch 只允许 add/update/remove；update 必须给出完整目标字段，不能把既有指标移到其他品目。用户更改来源为 user_defined，保留当前原始指令及理由；不伪装成文件抽取的要求。保存/确认遇到资料升版、基准变更、撤权或运行冲突会拒绝。

## 上下文、记忆和检索

- MySQL 保存真实消息、运行状态、版本绑定的 task_memory 和有来源的摘要；上下文按预算装配并记录所用来源和省略情况。普通聊天恢复同会话前文，不跨任务读取资料。
- 个人偏好沿用 `/api/v1/me/memories` 的有限键、明确确认、修改/删除与到期协议。本轮没有加入自然语言自动保存个人偏好、模板库或个人私有会话选择。
- 资料上传/解析完成后进入持久化索引队列。BGE-M3 在本机 Ollama 编码，Milvus 保存分块向量，BM25Plus 与向量结果按 RRF 融合；命中后从有权限的 SQL 原文回读。
- 索引未就绪或服务不可用时明确降级到词法检索。top-k 和向量近邻都不证明完整覆盖；没有直接原文时只说明“本次检索未找到”，不据此判采购证据不足。
- 正式审查走全量抽取及确定性规则；普通聊天意图选择由模型完成，提示约束不代表相关性/幻觉风险已经穷尽验证。

## 运行方式

先启动已有 MySQL、Redis、OIDC、Milvus 和 Ollama；`.env` 中设置已核对的 Embedding revision 和应用专用 Milvus 凭据，不能使用默认 root 凭据。

```powershell
python.exe -m alembic -c backend/alembic.ini upgrade head
docker compose -f compose.yaml -f compose.vector-app.yaml --profile agent build worker indexer cleaner
docker compose -f compose.yaml -f compose.vector-app.yaml --profile agent up -d --no-deps worker indexer cleaner
.\scripts\start-api-oidc.ps1
```

可选 overlay 为 worker/indexer 安装向量 SDK，indexer 循环复用数据库租约、manifest 和清理任务。API 从 `.env` 加载向量开关；容器通过 Docker Desktop 的 literal `host.docker.internal` 连接宿主 Ollama，不要求将 Ollama 改为公网监听。重启这些服务时应继续使用该 overlay。

indexer 的 Compose healthcheck 只检查配置；真实可用性应查看 index jobs/manifest 及一条端到端检索，不能把 healthy 当成索引成功。Milvus/Ollama/worker 停止时保留可见降级或未完成状态。本机集成验收不代表高可用、容量、真实多品类准确率或独立人工封存集已达标。

## 本机 Ollama 端口说明（2026-10-05）

本次 Windows 实际保留端口范围 11395–11494 包含 11434，导致 Ollama 启动报 socket access denied。已使用未占用的本地 11634；原 BGE-M3 文件与 digest 不变。应用 `.env` 的 `DOCUCHECK_EMBEDDING_BASE_URL=http://127.0.0.1:11634`，容器覆盖项为 `DOCUCHECK_DOCKER_EMBEDDING_BASE_URL=http://host.docker.internal:11634`。其他机器可沿用可用的默认 11434；两项必须对应同一个实际端口。

当前 Ollama 为本轮受控前台进程；重启电脑后需启动现有程序（已安装的目录如下），然后启动应用服务：

```powershell
$env:OLLAMA_MODELS = 'E:/Ollama/models'
$env:OLLAMA_HOST = '127.0.0.1:11634'
& E:/Ollama/ollama.exe serve
```

该终端保持运行。当前本机 API 与 Ollama 未安装为 Windows 服务，终端退出后需重新启动。API 脚本会读取 `.env`，Compose overlay 将容器地址替换为宿主桥接地址。
