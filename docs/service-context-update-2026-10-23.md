# 本机上下文改造服务应用

日期：2026-10-23。按用户“继续”的授权，将隔离分支已验收的 A1/A2a/A2b/B1/B2/C 固定提交 `b0923eca3ca8b25a917688c25775c24bb3d01716` 应用到本机 worker、API 和前端。源码未合并到主工作区，未推送。本记录只提交文档，运行源码继续固定在 b0923ec。

## 实际运行

- 页面：`http://127.0.0.1:15173/`，固定源码构建后的 Vite preview，`/api` 代理到本机 API。
- API：`http://127.0.0.1:8000/`，OIDC 模式；代码从本轮独立快照加载。
- worker：`docucheck-worker:context-b0923ec`，revision 为完整固定提交，健康检查通过。
- 当前维护启动入口：主工作区 `data/verification/context-service-deploy-20261023/start_entries.ps1`。该目录为本机忽略目录，不随 Git 发布；本机源码、构建、脚本和证据均在其中。旧日期入口保留追溯，不再用于当前服务。

本轮从原 worker 的配置在内存中取得环境，保留环境指纹、挂载、网络、运行用户、命令、健康检查和重启策略。没有读取或修改真实 `.env`，没有迁移或安装依赖。镜像基于保留的旧运行环境，构建使用 `--network=none --pull=false`；前端复用既有依赖，类型检查和生产构建通过。

配置窗口为 1,000,000 token；旧的默认单次 16k 和累计输入 48k 限制已取消。当前未设置累计输入/输出硬限。预留输出 4096 和 5% 安全余量时，系统允许 945,904 个估算输入字节；该数字是项目的保守准入口径，不是精确 token 数，也没有据此验证服务商真实支持 1M。

输入框下方新增“上下文 · 估算”入口。它读取当前对话最近请求的记录；旧历史没有新计量时显示未记录，须发送新问题才可能产生新记录。语义摘要与跨对话交接随代码应用，但本轮未触发真实模型整理或真实业务重问。

## 本轮核验

| 核验 | 结果与边界 |
| --- | --- |
| 固定源码 | 139 份 worker Python 源码与快照摘要一致；15 个 API 关键模块的加载位置和摘要一致；45 份前端源文件未变，10 份构建产物经 HTTP 下载与本地逐项一致。 |
| 服务与鉴权 | live/ready 200，首页 200；API、前端代理的匿名 me 返回 401，新 context-usage 路由匿名访问返回 401。浏览器实际到达中文 OIDC 登录页；没有使用用户密码，登录后的真实权限/页面交互尚待试用。 |
| 生产工具代码 | worker 内用内存脚本 Provider 执行 read_checklist → answer_checklist，合成苹果采购数量 20 箱短答和清单来源通过；配置预算检查通过。脚本决策每轮 2 次，两轮均为合成验证，真实模型请求 0。 |
| 旧历史 | 既有 completed 助手 Run `38e62788b6954658a6d35032d9f80afd` 经仓储重验可读，新用量查询返回 record=null；这是旧记录缺少计量，不是历史丢失。 |
| 数据保留 | 切换前、切换后及最终检查，所有业务表行数与内容摘要一致；654 份原件摘要一致。独立 indexer 持续运行，index_manifests 仅校验行数，清理调度字段不作不变承诺。库版本仍为 0018_upload_intents。 |
| 工作区与其他服务 | 主 HEAD fd8cfb7、六份原 WIP 的逐文件摘要保留；除 worker 换容器、dispatcher 暂停后恢复外，其余容器身份不变。 |
| 回退准备 | 保存旧镜像、独立 SQL 备份及原件清单；另用旧镜像创建未启动的回退容器，核对环境/挂载/网络/健康配置后删除。没有执行数据库恢复或实际在线回切，不把创建检查称为完整灾备验收。 |

本轮针对此前已验收的固定源码进行运行验证，没有重复离线全量。此前 C 的完整离线成绩为后端 2426 passed/59 skipped/网络 0、前端 302 项及类型/构建通过，范围见 [C 交付记录](context-usage-C-2026-10-23.md)。本轮没有真实模型调用，没有重跑正式审查或修改其结论。

本机 Ollama 11634 及上传解析宿主进程当时未运行，本轮未启动它们或自动处理旧待解析/索引任务。上下文改造的应用不代表语义检索、上传解析或完整采购链路已复验；资料问答仍需遵守既有降级提示。API 健康不等于这些外部能力全部可用。

## 启停与回退

从主工作区运行以下本机入口，启动或确认本轮 API/前端。worker 和既有数据库、身份、队列等依赖需已运行；入口不会读取 `.env` 或重建容器：

```powershell
& data/verification/context-service-deploy-20261023/start_entries.ps1
```

停止本轮 API/前端可执行同目录 `stop_entries.ps1`，它核对 PID 与命令来源后操作，不停止数据库或其他依赖。

如需回到切换前状态，先停止本轮 API/前端，确保无新上传、审查或队列任务，再运行：

```powershell
& E:/developTools/miniconda3/envs/docucheck/python.exe data/verification/context-service-deploy-20261023/deploy.py rollback
```

回退工具读取当前仍相同的运行配置，在排空后以保留旧镜像重建 worker，并恢复 dispatcher。切换前 API/前端原本未运行，因此回退至此前状态不要求启动旧页面；需要旧 API 时，本轮 start_api.py 的 `--rollback` 选择旧 516023d 快照。不要直接用主工作区源码启动或执行 Compose build 覆盖已固定版本。

已移除停止的旧 worker 容器，避免重复 Compose service 标签；旧镜像 `docucheck-worker:pre-context-20261023`、源码和配置指纹仍保留。临时环境文件已删除，SQL 备份仅留本机忽略目录，不纳入 Git。

## 本机证据

忽略目录 `data/verification/context-service-deploy-20261023/` 保存：before.json、backup.json、build.json/build.log、applied.json、api-runtime.json、frontend-build-fixed.log、verified.json、verified-final.json、runtime-source-and-cleanup.json、rollback-recreation-check.json 和登录页截图。前后两轮核验均通过；最终中文日志使用 UTF-8。构建初次因部署命令工作目录错误未找到依赖，修正绝对路径后成功，失败日志保留，未修改产品源码。

后续试用应登录页面，在原任务发送新问题后查看入口；旧对话不会被改写成新答案。真实请求、摘要效果与正式审查恢复仍须各自核对，不用脚本 Provider 或首页 200 替代。
