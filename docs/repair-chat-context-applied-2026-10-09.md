# 本机服务恢复与上下文修复应用

2026-10-09。用户回复“你帮我解决吧”，要求继续处理上轮服务启动及修复应用。应用源码为 `6601a71`，上轮交付说明为 `a0eb755`。本步没有修改业务代码、数据库结构、前端样式或依赖版本。

## 服务恢复

重新检查 API 8000 和前端 15173 均无监听，身份服务仍可用。隐藏后台 `Start-Process` 启动再次被自动审批返回 `blocked by policy`，命令未执行。随后采用工具管理的前台终端会话运行原服务，启动成功，未使用脱离管理的后台 helper。API 使用现有 `scripts/start-api-oidc.ps1` 的四项 OIDC 配置和项目 `.env`，没有关闭登录认证。

- API：`http://127.0.0.1:8000`，Python 进程 23176，工具终端会话 72515。
- 前端：`http://127.0.0.1:15173/`，工具终端会话 48175，strictPort 开启。
- 所有 HTTP 验证使用 `trust_env=False` 直连本机：前端 200、API live/ready 200，API及前端代理的匿名 `/api/v1/me` 都为 401；OIDC 发现接口 200。

这些是本机开发进程，不是开机自启或生产高可用部署。停止对应终端进程或退出运行环境后可能需要再次启动。浏览器打开请求由应用返回 queued，未把它算作实际页面渲染、登录或聊天验收。

## 应用后台镜像

沿用上轮已构建并验证的 `docucheck-{role}:chat-context-20261009` 镜像。激活前再次核对镜像 ID、revision 标签、原 latest、配置、非 root 用户、网络、挂载和 worker 只读 CA 指纹；没有拉取镜像或安装包。

更新前，queued/running Run、待派发、当前快照待解析、待索引和 Redis runs 队列均为 0。先停止 dispatcher，再确认 Celery active/reserved/scheduled 均为 0，正常停止并重建 worker/indexer/dispatcher/cleaner。使用 `compose.yaml + compose.vector-app.yaml + compose.model-tls.yaml`，`--no-deps --no-build --pull never --wait`，保留原证书验证配置。

| 角色 | 应用镜像 | 状态 |
| --- | --- | --- |
| worker | `8c400afc3c3749404b3dacdad56a62091fa1df6c206d2737fc90d93f040b7492` | healthy |
| indexer | `83f85620883d2a6ea1a089ec75413127232b4b6dc264fab150a8df34caacc47e` | healthy |
| dispatcher / cleaner | `dc289137bfe0e0f3d58179be351b0ada510640238040cbc33a8348b0c9b19678` | running；原配置没有 healthcheck |

四个角色各 119 个 Python 源码、合计 476 项哈希与当前应用源码完全一致，原三个文件差异清零。依赖名称和版本与原镜像逐项一致：worker/indexer 各 82 项，核心镜像 77 项。环境、命令、用户、挂载和网络保持一致。

## 验证和保留状态

- 实际新 worker 内只读加载原失败问题的七项基准、经既有校验的 MySQL 历史及原摘要，不回放个人偏好，完成上下文组装和预算预留：上界 15980，门限 16000，完整问题保留。
- 该检查没有调用 Provider，也没有生成业务 Run、结果或新 attempt；原失败 Run 状态、attempt、fence 不变。它证明新容器中的请求组装能通过，不证明真实模型回答质量。
- 两个 waiting_user 的 Run/attempt/fence 保留；五条索引仍 ready，派发分布和全部文档解析状态/claim/lease 指纹一致。
- 其他 14 个既有容器 ID、image、StartedAt 及状态完全一致。
- 目标手机会话的清单 head、保存确认操作及七个 Run 的状态、错误、attempt/fence 与排查前只读副本一致，没有新增该任务的 Run。
- API、前端及身份服务的最终 HTTP 检查通过。本步真实模型及 Embedding 请求均为 0。
- 上轮完整离线 **1286 passed / 41 skipped / 网络 0** 对应相同应用源码，本轮没有重新运行完整测试或前端构建。

## 启动与回退

当前默认后台标签已指向已应用镜像，可沿用既有三个 overlay 启动；无需再次构建：

```powershell
docker compose -f compose.yaml -f compose.vector-app.yaml -f compose.model-tls.yaml --profile agent up -d --no-deps --no-build --pull never --wait worker dispatcher cleaner indexer
```

需要回退时，先排空并暂停派发，再将四个 `docucheck-{role}:pre-chat-context-20261009` 镜像重新标记为对应 latest，使用相同三个 overlay 重建并复验。回退标签保留，未实际演练回退；不删除资料卷、数据库或凭据。部署脚本另外包含失败时恢复原标签及已停止服务的路径，本次未触发。

API 和前端手动重启方法仍为项目根目录激活 docucheck 后运行 `./scripts/start-api-oidc.ps1`，另一个终端进入 frontend 运行 `npm run dev -- --port 15173 --strictPort`。

## 后续范围

服务恢复与修复应用本步完成。未保存候选承接、“20 台”遗漏、空资料 revision 0 卡片时序仍需下一步修复；没有修改用户已确认的七项清单，也没有自动重试其失败对话。真实登录聊天/模型遵循程度、大清单上下文与原生产发布门槛仍按各自范围验收。

[脱敏证据](repair-chat-context-applied-evidence-2026-10-09.json)包含直连状态、镜像、排空、源码/依赖/状态验证及九份本地证据哈希；完整本地证据位于忽略目录 `data/verification/chat-context-applied-2026-10-09/`。上轮“已构建未部署”的说明作为阶段历史保留，当前运行状态以本记录为准。

独立只读复核已通过：九份证据哈希、当前六项 HTTP 状态、仅本机监听、四容器实际镜像及 476 项源码、源码提交归属、任务/索引/派发与目标清单记录的保留口径逐项一致。未发现 P1/P2 或必须修正的问题；复核没有调用模型或修改运行状态。
