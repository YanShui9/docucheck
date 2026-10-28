# 执行、记录与评分

本目录评估现有采购助手，不新增业务能力。数据、参考解、实际输出和评分结果分别保存。`reference_observations.json` 是 AI 辅助编写的40份评分器正例：结构字段引用预定量表、答案文字另行编写。它不是产品运行结果、独立人工标签，也没有据此校准 LLM judge。所有正例只验证“评分器能接受已知合理输入”，反例另验证错值、单位、否定、税、范围、状态与来源会被拒绝。

## 默认预览

在仓库根目录运行，默认不加载认证、不联网、不写业务数据：

```powershell
python -m evals.agent_interview.run --split development
python -m evals.agent_interview.run --split measurement --repeats
```

第二条列出30题首轮和预选6题的额外12次，共42个trial。开发10题、测量30题、重复18次（其中6次是首轮重用）和六项工程控制分别报告。

## 显式真实运行

先按 [README](README.md) 生成并校验全新原件目录。合法认证与跨任务用量账本属于本机部署配置；不把账号、Bearer、刷新token、完整环境变量或认证回调响应放入公共数据集。

执行入口只在显式 `--send` 后加载调用者指定的可信本地工厂。例如本机配置模块名为 `authorized_eval_client`、其零参数工厂名为 `create_adapter` 时：

```powershell
python -m evals.agent_interview.run --send --split development --output E:/eval-output/new-development-run --adapter-factory authorized_eval_client:create_adapter
```

此命令执行本地工厂代码，模块必须由运行者审核。工厂返回 `HttpAdapter`，注入合法认证客户端、已验证原件目录及真实用量守卫。客户端应实现 `request(method, relative_api_path, **kwargs)`，使用当前部署正常授权流程；工厂可包装 `close()` 释放认证客户端。禁止用禁用鉴权、回填数据库或把“只读成员”写进提示来代替真实权限前置。

`runtime_metadata` 只接受以下明确字段：application_commit、evaluation_commit、evaluation_source_sha256、dependencies_sha256、configured_model、model_configuration_sha256、parser_identity、vector_configuration_sha256。提交为40位Git SHA，摘要为64位 SHA-256。模型配置只保存非秘密的配置名及摘要，不能据此证明上游实际模型身份。`frozen_runtime()` 校验评测源码摘要和原件 manifest，并冻结 dataset/manifest 摘要；实际文档的 parser_version 另随证据保留。工厂负责从实际进程/镜像/配置和Git读取并核对这些身份，不能手填预期值冒充实查。

守卫必须在每次含模型的消息前核对累计账本、当前运行身份与无其他冲突任务；包括建立历史所需的真实消息。拒绝后整个协议停止新发送，剩余任务仍保留在固定分母。未知写入和仍活跃的Run同样停止后续任务，不自动重发。当前工具不提供自动跨进程续发；续轮或未知结果处理需要保留原协议与首轮记录，先只读核对，再明确登记新的执行计划，不能覆盖已有观察。

## 原始记录与恢复检查

每个trial创建独占目录。请求发送前保存journal、幂等键和正文；不保存授权headers。响应保存原文、状态、SHA，成功写入必须核对回执后ack。目录或journal已存在即拒绝复用。

```powershell
python -m evals.agent_interview.journal E:/eval-output/run/task-trial-1/requests.inflight.json
```

此命令只读本地journal，不联网、不确认未知写入、不发送模型请求。它列出已知Run回执、原幂等键和待核对状态。已发出的请求不能因为客户端未收到结果就认定失败。应按原回执读取服务端Run/消息/事件；没有确证前继续阻塞，不新建“替代”请求。

## 从实际观察评分

`HttpAdapter.run()` 返回原始观察，尚未认证自然语言语义。用 `observation.normalize(dataset, raw, source_hashes=...)` 将真实document、SHA、parser、位置和引用对应到冻结原件；source_hashes 必须来自实际校验通过的原件manifest。归一化同时检查保留的journal摘要及实际HTTP对象一致，不能把gold事实填回真实观察。

语义/行为复核单独保存annotation，包含确切 observation SHA、reviewer/method、结论和可解析JSON pointer。`annotate()` 禁止覆盖state和usage；评分入口重验标注绑定，防止答案改动后沿用旧判断。可编辑的哈希不构成独立签名，也不能证明评审者身份。真实观察未标注时保留pending或失败；`authored_fixture` 不能认证真实运行，未校准 `agent_review` 仍待语义复核。人工复核数量只能来自真实人工操作。

`grade_task()` 精确区分五种业务状态、每个供应商/品目/指标和各格必要原文，完整矩阵不得投影隐藏。`aggregate()` 保留10/30固定首轮分母、分层和重复明细，未知用量不会变零或使整份报告消失。工程控制通过 `python -m evals.agent_interview.controls` 独立运行，其六场景共享四个隔离pytest节点，不是六次真实业务运行。

```powershell
python -m evals.agent_interview.report E:/eval-output/run/report.json E:/eval-output/run/report.md
```

时延分为准备、任务及总耗时，包含排队、失败与超时。实际API tokens与未记录用量分别报告；Embedding与账单缺失时保持未知。小样本Wilson区间仅描述该组计数，不代表生产流量准确率。数据和失败样本公开后，不再声称它们是未见封存集。
