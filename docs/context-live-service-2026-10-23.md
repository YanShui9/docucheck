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
- 登录等待属于先前阶段：专用会话曾变为空白页，重开后一度仍在登录表单；用户再次登录后，已实际进入工作区并完成下面的有限复测。没有读取密码代填，也不推断会话丢失的原因。等待时的只读基线仍保留在`case-before-retest-01.json`。

## 登录后有限复测

运行源码保持1328140。本轮新增4条页面请求及1次显式重试，未修改已确认清单、资料对应或原件。新建了同采购任务的第二段对话；页面把任务列表标题自动改为这段对话的首条问题，未手动改名。

| 场景 | 实际结果与边界 |
| --- | --- |
| 已确认采购数量 | Run `044215585e6d43549f907af8e200a999` completed，返回20箱并附清单依据，数量读取通过。但正文重复必选/启用等配置，还将既有`conditions=["20"]`扩写为“适用条件：20箱”，答复质量不能判完全通过。没有改旧异常条件。 |
| 真实语义摘要 | 同Run的摘要调用完成，`conversation_summaries`确有6条notes，manifest绑定其ID/版本，路由实际选用6条notes和8轮原话。不是仅看到模型说“记得”。后续回读失败Run也保存了更新后的摘要；保存摘要不保证所有历史查问成功。 |
| 用量入口 | 当前对话最近一次请求显示13.7 KB / 926.2 KB、1.5%，配置窗口1,000,000 token，输出预留1536、安全预留50000；API输入3532/输出94/合计3626 token，与后台该次路由事件一致。整Run另含摘要和前一次路由，不能用这张卡代替整Run总量。 |
| 最早原话回读 | Run `79439bdce7464bf2a59526194bd3a68a` partial/history_scope，直接把内部错误显示给用户；不通过，没有盲重试。现有事件没有保存所选历史工具及参数，不能断言是模型选错工具、消息ID或哪项范围校验失败。 |
| 新对话承接 | Run `66dd1bb7d74f4716ac62b0614a951a2b`已生成并选入同Case交接，来源含上一段最后请求；询问该请求仍partial/history_scope。说明交接生成/注入存在，但端到端回忆未通过，不能因记录存在就称跨对话体验完成。 |
| 正式审查首次 | Run `563fd1b6514d49dcbc07be58900f8322`登记review并进入Dynamic，首次抽取有1项evidence_relationship拒绝，结果partial/coverage_incomplete；未完整生成正式结论。 |
| 正式显式重试 | 同Run第二attempt重新登记review，继续decide/extract/有限repair，未退回清单问答；重试意图保留通过。但本次有2项fact_criterion拒绝，仍partial/coverage_incomplete，不能称正式审查质量通过。 |

上述新请求无context_budget/input_budget/output_budget，未知响应0。这个小资料场景没有逼近窗口阈值，也没有触发context_repack，不能据此声称真实大窗口、缩批或全部长期记忆能力通过。跨资料/清单版本变更实验、新上传解析及完整向量检索仍未验。正式答复仍出现长证据ID和逐格原文，属于尚未关闭的可读性问题。

浏览器工具保存截图时曾无响应，随后通过已存在浏览器的调试端口建立`context-live-reconnect-20261023`连接，保留原登录页面；没有因工具故障重发业务请求。本轮没有成功保存该截图，不把截图作为证据。

本次验收及诊断累计16次真实模型请求（原4次加本轮12次），输入56544/输出4192 token、未知0；失败和显式重试均计数。该口径不是全局预警轮总量，数据库事件可能含脚本Provider，不能直接替代真实调用账本。Embedding显式请求仍为1次，独立indexer自动请求另计、未汇总。新增账本`model-ledger-03.json`保留前版，不覆盖原失败。

后续建议先做一个后端小步骤：记录有限的历史工具/失败阶段诊断，区分本对话与任务交接的可读来源；只对已授权的消息句柄进行有界恢复，不扩大Case/版本权限，不让历史请求授权执行，并为最终不可回读提供中文说明。须先复现具体失败路径，再单独修复和实连复测；本轮没有凭猜测修改生产代码。正式候选校验失败和答案可读性分别保留后续，不以取消守卫解决。

## 回退准备与证据

旧镜像 `docucheck-worker:context-b0923ec` 保留。需要回退时先确认无新上传/Run并停止当前API（仅PID与start_api.py命令来源匹配时），再运行本轮 `deploy_patch.py rollback-worker`，最后启动原目录的API/前端入口。该回退代码保留环境和挂载，尚未执行在线回退或恢复业务数据库，不称灾备通过；不要误用原部署脚本退到更早的516023d。

本机证据目录：`data/verification/context-live-20261023/` 保存首次Run、两次模型诊断、投影反例、调用账本、冻结源码与离线门禁。本轮另存case-retest-01、case-history-read-01、case-cross-conversation-01、case-formal-01/02、case-formal-retry-01/02和acceptance-summary-01；中间running快照保留，终态以最后快照为准。`collect_case_readonly.py`仅窄查指定Case的Run/事件/attempt/摘要/助手与审查结果，不调用模型或写库。`data/verification/context-live-repair-deploy-20261023/` 保存source/before/drained/applied/verified-01/source-receipt-01、独立快照和启停/回退入口。业务原文、SQL备份和配置凭据不提交Git。

主HEAD仍fd8cfb7，六份既有WIP逐文件摘要核对保留。本记录为本地隔离分支文档更新，运行源码继续固定1328140。
