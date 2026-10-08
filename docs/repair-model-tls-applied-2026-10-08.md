# Worker 本机证书配置应用与实连验证

日期：2026-10-08。应用源码基准：`38c9f03`。用户在了解本机根证书的用途后回复“好，那你来帮我做吧”，授权应用上步已核对的信任配置。本步未修改业务 Python 或前端代码。

## 已应用的配置

本机被忽略的 `.env` 已配置 `DOCUCHECK_MODEL_CA_BUNDLE`，指向共享 data 目录外的 `.local-trust/model-ca-bundle.pem`。仅 worker 增加 `SSL_CERT_FILE` 及只读绑定挂载；证书和私钥内容均未纳入 Git，没有修改 Windows 根库或宿主进程的 SSL 环境。

公共 CA 与经核对的 Kaspersky 本机根合并为 151 张 CA。bundle SHA-256：`dc611ea7e32e13cc5f9a3b27aabc0837f2c4a2a86bb04633e10d8bf1869ce636`。根证书指纹及信任范围见[上步根因记录](repair-model-tls-2026-10-08.md)。证书与主机名验证均保持开启。该配置不能直接复制给其他机器；证书轮换后须重新核对来源、指纹与有效期。

## 镜像与容器验证

常规 Compose build 因 Docker Hub 认证端点连接超时，未完成在线基础镜像构建。本步改用本机正在运行且依赖已验证的镜像 `sha256:59e8e37c82cb6172938ae178780c94b53e99da342d974b7ab032f7df6c4ba895` 作为恢复基础，COPY 当前 backend/app，使用 `--pull=false --network=none` 完成离线应用构建。没有安装或升级包；构建中 pip check 和四个后台入口 import 通过。

实际 worker 镜像：`sha256:d1930f8f1d16f7f04493b4f0a340632fe31dfee2cfda17b85688ecbd5cab9c72`。容器以 docucheck 用户运行、healthy，118 个 Python 源文件哈希与宿主一致。其余 6 个 Compose 容器 ID 未改变。重建前确认没有 queued/running Run，原 waiting_user 任务保留。

这是使用已有运行环境的本地恢复，不代表常规 Dockerfile 在线可重建、全新依赖安装或生产镜像发布已通过。Docker Hub 连接恢复后仍应重新执行标准构建。

## 本轮验证结果

- 一次临时容器预检查及一次实际 worker 检查：使用 SDK 的 DefaultHttpx2Client，不带认证 GET `/models`，均返回预期 401；TLS 证书与主机名验证成功。这两次不属于模型生成调用。
- 实际 worker 内执行项目 `app.provider_probe` 一次：仅发送合成句子“已装内存 16 GB”，强制工具调用返回 MemoryProbe / 16，input_tokens=304、output_tokens=35，约 2.282 秒。最多输出 128 token，无自动重试，结果已知；实际费用以服务商账单为准。
- 当前定向回归 **138 passed / offline_network_attempts=0**；全量 backend/evals/scripts Ruff 和容器 pip check 通过。上步全量 1266 passed / 41 skipped 属于 `38c9f03` 的既有证据，本步没有重跑全量，也未重复前端测试。
- Compose 合并对比确认其他服务、worker 原网络及 data 挂载保留，仅增加只读证书配置；宿主文件缺省创建关闭。环境变量显式置空时配置拒绝，避免本机 .env 已配置导致负例无效。
- 独立只读复核未发现阻断问题：6 个交付文件与 11 个本地证据摘要一致，证据哈希逐一匹配；核对最小探针调用上限、配置作用域、缓存构建限制和业务未完成状态，没有将探针成功扩大为完整采购审查通过。

## 尚未完成的业务验收

保留的合成苹果采购任务 `832da6f4a512476c86a2cfc79a7fe30e` 已确认两项指标。点击原失败审查的“重新处理”时登录会话过期，页面转入中文 OIDC 登录页；只读数据库核对未新增 attempt 或模型调用。原审查仍为 partial，前两次失败记录和结果未知状态保留。

已请求用户在本机重新登录，无需发送账号密码。登录恢复后再显式重试一次，验证本次新报告、来源和导出，记录实际调用和用量。本轮最小探针成功不能替代苹果审查通过。Milvus/Ollama 当前实连、真实多品类泛化和生产容量验收也未在本步完成。

## 后续启动与回滚

本机使用该信任配置时，须保留三个 overlay；本轮镜像已构建，因此实际重建命令为：

```powershell
docker compose -f compose.yaml -f compose.vector-app.yaml -f compose.model-tls.yaml --profile agent up -d --no-deps --no-build worker
```

该命令不更新应用源码。后续代码更新需先成功构建对应镜像，再重建。重建前检查是否有执行中的任务。

移除本机信任配置时，省略 TLS overlay；不删除数据库或原件：

```powershell
docker compose -f compose.yaml -f compose.vector-app.yaml --profile agent up -d --no-deps --no-build worker
```

该回滚仅撤销运行时额外信任，不回滚镜像源码；撤销后原证书问题可能再次出现。不得以关闭 TLS 校验作为替代。

原始脱敏结果位于被忽略的 `data/verification/model-tls-applied-2026-10-08/`；[可提交证据摘要](repair-model-tls-applied-evidence-2026-10-08.json)记录镜像、检查结果与本地证据文件哈希。每步独立提交本地 Git，实际提交号以日志和交付回复为准。
