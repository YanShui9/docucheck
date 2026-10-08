# 实连验收第二部分 A：本机前端与 OIDC 回调

日期：2026-10-08。基准提交：`4d5e404`。本步修复登录前置条件，后续登录后的页面/模型/向量链路仍按[实连计划](superpowers/plans/2026-10-07-repair-live-acceptance.md)继续。本步单独提交本地 Git，提交号以 Git 日志为准。

## 发现与修复

Windows 的 IPv4 排除端口范围5141–5240包含5173，前端此前报 `EACCES`。改用默认15173，不修改系统保留端口。Vite仅监听127.0.0.1，启用 `strictPort`，端口冲突直接失败。

前端和本机 OIDC 工具共用根 `.env` 的 `DOCUCHECK_WEB_PORT`；进程环境变量优先，范围1–65535，仅接受字面十进制。独立复查发现最初使用Vite `loadEnv` 会额外读取模式文件和 `.env.local`，与Python不一致；最终改为Node内置 `parseEnv` 精确读取根文件，Python对端口单独禁用插值，密码解析保持原逻辑。回归覆盖模式覆盖漂移、默认值、非法值和插值拒绝。

新增命令：

```powershell
python.exe -m backend.app.dev_oidc_realm --sync-client
```

此命令固定连接本机8080的既有 `docucheck-demo` realm，只更新 `docucheck-web` 的 `redirectUris`、`webOrigins` 和 `post.logout.redirect.uris` 并读取回验；保留完整客户端的其他设置。它不调用用户修改或realm重建接口，不生成口令。HTTP禁用代理环境和重定向，失败返回稳定错误码，不输出响应/token/口令。实际已同步至15173，无需重启Keycloak。部署型SSO管理不使用此本机演示工具。

## 已验证

- 修改前12项新后端测试均失败，复现旧回调/端口缺少校验/缺少同步能力；实现后通过，复查后新增插值用例。
- 最终OIDC工具及认证定向：**14 passed**。
- 最终前端：**144 tests / 13 files passed**，vue-tsc及Vite生产构建通过。
- 本轮完整后端离线 **1260 passed / 41 skipped / 网络尝试0**，pip check/完整Ruff通过。该进程启动于独立复查后的精确根文件/插值解析修正之前，不能当作最终解析实现的全量运行；最终解析修正由上述14项后端定向、144项前端及构建重新覆盖。41项外部服务测试不算通过，本步没有重跑上一部分MySQL实连成绩。
- Ruff通过；独立只读复查确认配置漂移问题关闭，无新增实质缺陷。
- 本机真实前端15173返回200；API live/ready为200，经Vite代理未登录 `/api/v1/me` 返回401。
- 新回调返回中文登录页200，旧5173和外部域名回调均400。
- 缺少PKCE时返回302，跳至合法前端并携带 `error=invalid_request`，不签发授权码。初始探针错误地预期400，已根据实际OIDC错误回传语义修正断言，未修改PKCE安全设置。
- 本机客户端仍要求S256，直接密码授权仍关闭；退出回调配置回验通过。**尚未实际执行登录后退出流程**。
- 生产构建产物中未找到本机已配置的密码/API key/token值；此项是精确值泄漏检查，不替代全仓安全扫描。

原始脱敏HTTP结果：`data/verification/repair-local-login-live-2026-10-08.json`。提交的[机器证据](repair-local-login-evidence-2026-10-08.json)绑定本步源码及原始结果哈希。全量后端离线结果与其运行版本范围也列于该证据。

## 当前运行与待做

前端运行于工具终端 `http://127.0.0.1:15173/`，API仍为8000，Keycloak仍为8080。没有启动/重启Docker服务，没有修改页面视觉或业务交互，没有调用DeepSeek或Embedding，也没有重启既有Milvus/Ollama。

浏览器已到达真实OIDC中文登录页，已请用户使用本机 `.env` 中的演示口令完成登录，不发送密码。本步不把登录页可达声称为完整登录成功。用户登录后继续清单编辑/保存确认、候选历史、记忆有效期、报告/原文/导出的浏览器验收；之后另做已说明数据与预算的模型及BGE/Milvus抽样。

本次后端改动仅为宿主执行的开发身份工具，API/worker业务模块未修改；不沿用前一步“全部容器源码与宿主一致”来证明本步新增工具已进入容器。

实现参考：[Vite端口与strictPort](https://vite.dev/config/server-options#server-port)、[Keycloak JS适配器的PKCE/回调配置](https://www.keycloak.org/securing-apps/javascript-adapter)。
