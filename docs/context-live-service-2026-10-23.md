# 上下文真实验收及补修应用

日期：2026-10-23。承接[原服务应用](service-context-update-2026-10-23.md)。用户已完成过真实登录；原任务第一条新请求发现清单查询未命中，随后按[补修记录](context-live-repairs-2026-10-23.md)关闭两个可复现边界。

## 当前运行版本

API与worker已应用固定提交 `1328140bd9735da63cc98852cf71eb77501985b9`，未合并主工作区、未推送。worker139份源码与Git快照逐项一致；API从新快照加载，核对助手、清单读取与执行模块路径/摘要。worker健康、API live/ready与首页200，匿名me401。

页面仍为 `http://127.0.0.1:15173/`。前端构建、Ollama和解析进程保持原运行版本/入口；这三份Python改动不涉及解析或前端源码，不能据此声称所有进程都重新部署。

当前API/前端维护入口是主工作区忽略目录：

```powershell
& E:/PycharmProjects/docucheck/data/verification/context-live-repair-deploy-20261023/start_entries.ps1
```

它核对端口PID/来源/固定提交再启动API，前端复用原b0923ec构建。旧 `context-service-deploy-20261023/start_entries.ps1` 固定了旧后端身份，不能继续用作新版本启动入口。Ollama和解析的本轮启动记录仍在 `data/verification/context-live-20261023/`。

切换前排空Run/Outbox/worker，暂停dispatcher后换worker，再恢复dispatcher。模型配置、环境指纹、挂载、网络、运行用户、命令、健康检查和重启策略均核对保留。切换窗口内业务表内容摘要与654份原件一致；index_manifests仍只核对行数，独立indexer调度字段允许变化。没有迁移、安装依赖或读取真实.env。

## 已测和待测

- 原真实问题未正确回答20箱，记录保留，不计通过。
- 两次受限诊断重现组合查询未命中，并定位首次摘要与截断摘录比较的限制；后端离线2431通过/59跳过、相关83项和独立复核通过，不能替代部署后真实模型复测。
- 用量卡片实际显示了最近请求的17.1KB/926.2KB估算输入、1.8%、配置窗口100万、API输入4645/输出52token；与后台该次路由调用一致。该卡片是最近一次请求，不是整Run总量。原Run还含摘要调用，整Run输入6675/输出606token。
- Ollama已在E盘模型目录恢复，显式BGE-M3请求得到1024维有限数值；新上传解析、持久化向量检索全链路未验。
- 准备补修后真实重问时，专用浏览器会话变为about:blank，重新打开要求登录。已重开可见窗口并请用户登录；没有读取密码代填。**截至本记录，补修后的页面重问、真实摘要成功保存、原话回读、跨对话/版本及正式审查恢复均待验。** 不把登录中断当模型失败，也不推断浏览器会话丢失的原因。
- 用户回复“已登录”后，再次读取专用会话`context-live-20261023`仍为中文登录表单，唯一标签页也在OIDC登录地址。已请用户核对是否登录另一个窗口；没有提交新问题。只读查询原Case最新Run仍是首次失败的`85b18809ff52430984dec9ba6698ee4a`，证据另存`case-before-retest-01.json`，本轮请求账本保持4次。

本次已确认4次真实模型请求，输入10959/输出1164token、未知0；该口径仅覆盖本次验收及诊断。全局预警轮不能用数据库事件条数直接替代，事件可能含脚本Provider。Embedding显式请求1次，独立indexer自动请求另计，尚未汇总。

## 回退准备与证据

旧镜像 `docucheck-worker:context-b0923ec` 保留。需要回退时先确认无新上传/Run并停止当前API（仅PID与start_api.py命令来源匹配时），再运行本轮 `deploy_patch.py rollback-worker`，最后启动原目录的API/前端入口。该回退代码保留环境和挂载，尚未执行在线回退或恢复业务数据库，不称灾备通过；不要误用原部署脚本退到更早的516023d。

本机证据目录：`data/verification/context-live-20261023/` 保存首次Run、两次模型诊断、投影反例、调用账本、冻结源码与离线门禁；`data/verification/context-live-repair-deploy-20261023/` 保存source/before/drained/applied/verified-01/source-receipt-01、独立快照和启停/回退入口。业务原文、SQL备份和配置凭据不提交Git。

主HEAD仍fd8cfb7，六份既有WIP逐文件摘要核对保留。本记录为本地隔离分支文档更新，运行源码继续固定1328140。
