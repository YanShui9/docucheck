# A2a：模型容量与任务累计额度分离

> **For agentic workers:** 使用 executing-plans 在当前隔离工作区逐项实施；按 requesting-code-review 技能另做一次独立只读复核。以失败回归、相关检查和离线全量为出口。

**Goal:** 移除 standard/thorough 中隐藏的 16k/24k 单次限制及 48k/96k、6k/12k 累计硬限，以配置的模型窗口组织请求，只有显式任务配额才阻止继续调用。

**Architecture:** 沿用 Budget 与 A1 的请求诊断。没有经当前模型/接口验证的分词器时，继续采用明确标注的 UTF-8 保守估算：一个配置 token 的输入空间只允许一个估算字节，保留输出及 5% 窗口安全空间；这不是精确 token 计数，也不保证能装满模型标称容量。实际 usage 单独统计。所有入口使用相同的窗口和显式配额配置。

**Tech Stack:** Python 3.12、现有 LangChain Provider、pytest、Ruff；不安装依赖、不访问业务库、不调用模型。

---

## 范围与后续

本步是总体 A2 的第一个可单独验收部分。A2b 负责完整请求驱动的原文/指标缩批、有界恢复及整理阈值；B 才做语义摘要；C 的前端另行讨论。本步不宣称已经自动压缩或解决所有大请求。沿用 A1 隔离分支，不合并主工作区的资料关联 WIP，不推送、不部署。

## 1. 预算与失败测试

文件：修改 `backend/app/agent/policy.py`；新增 `backend/tests/test_context_capacity_policy.py`。

- [x] 写失败测试：1M 配置允许超过 16k 的请求；小窗口会拒绝；输出与 5% 余量均计入；多次已知/未知 usage 越过旧累计上限仍可继续；显式上限分别报 input_budget/output_budget；取消、deadline、调用次数继续拦截。

```python
budget = Budget(context_window=1_000_000)
messages = [HumanMessage(content="采购要求" * 3000)]
options, size = budget.reserve(messages, [], output_cap=4096)
assert size > 16000
assert budget.prompt_limit(4096) == 945904
assert options["max_output_tokens"] == 4096
```

- [x] 运行：`python -m pytest backend/tests/test_context_capacity_policy.py -q -p scripts.offline_guard`，先确认失败原因是原固定额度。
- [x] 默认 max_input/max_output 为 None；默认 max_prompt 随 context_window 派生。`planned_output` 集中处理显式剩余额度；`prompt_limit` 使用 `min(显式输入上限, 模型窗口-输出-向上取整的5%余量)`。
- [x] `record` 收到实际 usage 才校正；未知 usage 保留预留，不记成0；实际累计超过显式配额停止，不能自动重发。显式配额为配置整数，非法类型/范围拒绝。
- [x] 同一命令确认通过，再运行 `backend/tests/test_agent_policy.py`。

## 2. Provider 配置与全部入口

文件：`backend/app/providers/config.py`、`chat_model.py`、`backend/app/agent/assistant.py`、`dynamic.py`、`graph.py`、`context.py`、`compose.yaml`、`.env.example`；新增 `backend/tests/test_context_capacity_paths.py`，复用 `test_provider_contract.py` 的合成配置并回归该文件。

- [x] 添加失败测试：未配置或空白总限额时为 None；`DOCUCHECK_RUN_MAX_INPUT_TOKENS` / `DOCUCHECK_RUN_MAX_OUTPUT_TOKENS` 为正整数时生效；0、负数、小数拒绝，不回显配置；标准/深入模式只控制行动次数。空白表示未配置，使 Compose 可透传可选变量；安全百分数单独校验。
- [x] 将上述可选配置经 Provider 传给统一 `Budget.for_provider`；不读取或修改 .env。图/聊天/动态审查均用该入口，显式注入 Budget 的离线测试仍可控制边界。
- [x] 所有历史选择与裁减改用 `budget.prompt_limit(output_cap)`；`context.assemble` 无参数时也按模型容量计算，不能残留 16k 默认值。保留完整问题、完整规则及工具调用/结果配对。
- [x] 通过真实 Agent + FakeProvider 验证大请求可达 Provider、显式配额在本地拒绝、未知结果只调用一次，及小窗口裁减仍保留当前问题。
- [x] 独立复核补修：动态探针 `CappedProvider` 同样透传两个累计限额及安全余量，兼容旧 Provider；先观察3项失败，再确认64项相关回归通过，原6次调用上限保留。
- [x] 全量反例补修：两套评测的离线准备不再从模式取累计额度；目标服务的可选总额未核实时记录null并明确来源，保留调用次数计划及旧报告。原2项失败回归后，两文件55项通过。

## 3. 诊断兼容与文档

文件：`context_usage.py`、`diagnostics.py`、`backend/tests/test_context_request_usage.py`、配置文档、总体设计与交付记录。

- [x] A1 诊断继续可读；新增有限枚举标明字节估算及一字节/token的保守准入策略、5%输出外余量、显式任务配额。无限额的剩余值为 None，不能伪造为0或巨大数字。白名单投影拒绝正文/凭据和任意字段。
- [x] 旧 A1 测试需验证固定配额时显式传入原值，旧记录的既有字段与错误码仍可回读；新增测试覆盖无配额与有配额。
- [x] 记录这是估算，未实测第三方1M输入；不生成精确占比。说明后续 A2b/B/C 的边界。

## 4. 验证、审查、本地提交

- [x] 定向：policy、provider、三入口、context_request、上下文裁减、未知结果保护；探针与两套评测准备的后续回归也通过。
- [x] 自审及独立复核所有 max_input/max_output/max_prompt 消费点、reserve/record、修复请求、权限/来源检查和缓存身份；只调整预算，不改变审查规则。复核发现的探针配置透传P2已补修，并独立验证关闭。
- [x] `python -m scripts.verify_offline --report E:/PycharmProjects/docucheck/data/verification/context-capacity-A2a-20261022-03.json`退出0：2237 passed/56 skipped/0 failed、网络尝试0，依赖/Ruff通过；01的2项失败及02跨日无最终报告的中断记录保留，不改前端，不重复其构建。
- [x] 记录源码/运行版本区别、跳过项和真实模型0次；主工作区6份 WIP校验一致；按既定授权只本地独立提交，保留隔离分支与工作区，最终回执绑定提交/报告/27份源码清单。
