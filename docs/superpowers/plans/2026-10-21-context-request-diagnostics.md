# 上下文第一步：完整请求计量与拒绝诊断实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本计划仅供审阅，尚未授权执行；不派发子代理。

**Goal:** 每个准备调用模型的请求，在预算检查前留下同一口径的大小、限制、阶段和结果；明确区分请求前拒绝、已进入 Provider、已返回用量和未知结果。

**Architecture:** 新建纯计量模块，围绕已有 `Budget.reserve()` 记录诊断；聊天、动态审查和旧兼容流程接入同一契约。通过现有 trace/Run 事务保存有限数字诊断，不新增数据库表，不改业务快照或证据摘要。

**Tech Stack:** Python 3.12、LangChain messages、pytest、现有 SQLAlchemy/Run 事件持久化。

**Design:** [总体设计稿](../specs/2026-10-21-context-management-design.md)。本步是 A1；A2 才启用新窗口与累计额度策略，B 实现语义摘要，C 经用户确认后实现前端用量入口。

---

## 范围与验收

本步完整实现“看得清每次请求为何被拒绝”，不改 16,000/48,000 的当前执行值，不新增模型调用，不把字节估算称为精确 token，不宣称本步已关闭所有上下文中断。

本步的“完整请求”指组装完成后的业务消息与工具定义，包含新增的修复说明；沿用 `input_bound()` 的字段投影和固定开销进行 legacy 对照，不宣称准确复现 Provider 的最终网络封装或模型分词。A2 再核对 Provider 消息转换、工具封装和匹配的计量方式。

- [ ] 聊天、动态正式审查、格式修复、语义修复和旧兼容流程在 `reserve()` 拒绝前均有诊断。
- [ ] 当前问题、修复说明、工具定义和消息开销计入完整请求；沿用旧估算算法作对照。
- [ ] 记录模型窗口 token、legacy 估算单位/值、单次限制、任务剩余额度、输出预留，分别命名；不生成跨单位百分比。
- [ ] 本地拒绝时 Provider 调用为 0，诊断为 rejected；真实 usage 未返回时保持未知。
- [ ] 原有 source/version/attempt/fence/权限校验继续约束落库；旧历史无新字段仍可读取。
- [ ] 诊断只存有限数字、枚举和请求指纹；不存原文、完整问题、工具参数、偏好内容、凭据。
- [ ] 不混入资料关联引导的已有 WIP；源码检查和服务部署分别报告。

## 文件分工

| 文件 | 动作和责任 |
| --- | --- |
| `backend/app/agent/context_usage.py` | 新建：纯计量和带诊断的预留适配器；不负责压缩或重试 |
| `backend/app/agent/assistant.py` | 接入聊天 routing/edit/evidence 请求；沿用已有阶段与错误处理 |
| `backend/app/agent/dynamic.py` | 接入 decide/extract，每次格式/语义修复重新测量 |
| `backend/app/agent/graph.py` | 接入旧兼容 AgentSession，避免旧入口漏测 |
| `backend/app/agent/diagnostics.py` | 新诊断字段白名单，保留旧字段；明确允许 context_budget/token_budget 等现有错误码 |
| `backend/tests/test_context_request_usage.py` | 新建：计量契约、拒绝和诊断脱敏测试 |
| `backend/tests/test_context_request_paths.py` | 新建：三条流程及修复请求集成反例 |
| `backend/tests/test_context_request_persistence.py` | 新建：Run 事件落库、旧历史兼容和权限/版本反例 |

现有 `RunRepository.finish()` 已事务写入 `safe_trace_events()` 过滤后的事件。本步优先复用这条路径；只有持久化测试证明接口缺口时才修改 `run_repository.py` 或 executor，并在交付记录列明具体原因。

## Task 1：固定基线并建立失败回归

- [ ] 读取根入口、设计稿、当前 Git 与原 WIP；在实现开始时按 using-git-worktrees 技能创建隔离工作区。以当时已提交基线开始，不复制另一任务未提交的资料关联修改。
- [ ] 先新增纯计量测试。下面是最低测试契约；模块不存在时应先出现导入失败。

```python
from langchain_core.messages import HumanMessage, SystemMessage

from backend.app.agent.context_usage import measure_request
from backend.app.agent.policy import Budget, input_bound


def test_measure_complete_request_without_claiming_exact_tokens():
    messages = [SystemMessage(content="仅依原文回答"), HumanMessage(content="苹果多少箱？")]
    schemas = [{"type": "function", "function": {
        "name": "read_checklist", "parameters": {"type": "object"},
    }}]
    budget = Budget(context_window=1_000_000)
    value = measure_request(messages, schemas, budget=budget,
                            output_cap=1536, phase="routing")
    assert value["legacy_input_estimate"] == input_bound(messages, schemas)
    assert value["model_window_tokens"] == 1_000_000
    assert value["estimate_method"] == "serialized_utf8_plus_512_v1"
    assert value["exact_tokens"] is False
    assert value["phase"] == "routing"
    assert "used_percent" not in value
    assert "苹果" not in str(value)


def test_repair_instruction_changes_request_measurement():
    original = [HumanMessage(content="读取报价")]
    repaired = [SystemMessage(content="只修正输出格式，保留原文值与引用"), *original]
    budget = Budget()
    first = measure_request(original, [], budget=budget, output_cap=512, phase="extract")
    second = measure_request(repaired, [], budget=budget, output_cap=512, phase="extract")
    assert second["legacy_input_estimate"] > first["legacy_input_estimate"]
    assert second["request_digest"] != first["request_digest"]
```

Run（仓库根目录，PowerShell）：

```powershell
& E:\developTools\miniconda3\envs\docucheck\python.exe -m pytest backend/tests/test_context_request_usage.py -q -p scripts.offline_guard
```

预期：先因新模块不存在而失败；没有网络请求。随后修复新增模块，不通过跳过测试得到通过。

## Task 2：实现纯计量与拒绝前事件

- [ ] 在 `context_usage.py` 实现以下最小算法。字段的单位以名称和 estimate_method 区分；不输出 token 百分比。

```python
import json

from ..evidence.repository import digest
from .policy import RunStopped, input_bound

PHASES = frozenset({"routing", "edit", "evidence", "decide", "extract"})


def measure_request(messages, schemas, *, budget, output_cap, phase):
    if phase not in PHASES:
        raise ValueError("invalid_context_phase")
    schema_bytes = len(json.dumps(schemas, ensure_ascii=False).encode("utf-8"))
    planned_output = min(output_cap, budget.max_output - budget.output_reserved)
    return {
        "version": 1,
        "phase": phase,
        "estimate_method": "serialized_utf8_plus_512_v1",
        "exact_tokens": False,
        "model_window_tokens": budget.context_window,
        "legacy_input_estimate": input_bound(messages, schemas),
        "legacy_schema_bytes": schema_bytes,
        "legacy_prompt_limit": budget.prompt_limit(output_cap),
        "legacy_task_input_remaining": max(0, budget.max_input - budget.input_reserved),
        "output_requested_tokens": output_cap,
        "output_reserved_tokens": max(0, planned_output),
        "request_digest": digest([
            [message.model_dump(mode="json") for message in messages], schemas,
        ]),
    }


def reserve_with_context(budget, messages, schemas, *, output_cap, phase, events):
    measurement = measure_request(messages, schemas, budget=budget,
                                  output_cap=output_cap, phase=phase)
    event = {
        "kind": "model", "operation": "extract" if phase == "extract" else "decide",
        "status": "rejected", "provider_called": False, "context": measurement,
        "sequence": len(events) + 1,
    }
    events.append(event)
    try:
        options, size = budget.reserve(messages, schemas, output_cap=output_cap)
    except RunStopped as exc:
        event["error"] = exc.code
        raise
    event["status"] = "started"
    return options, size, event
```

`legacy_task_input_remaining` 是当前预留和 usage 校正混合机制的数值，只用于解释旧机制；不可当作精确剩余 token。A2 将替换其语义，旧字段保留作历史诊断。

- [ ] 增加本地拒绝测试：使用 `max_prompt=1` 和足够的 `max_input`，断言 `RunStopped.code == context_budget`、事件 rejected、`provider_called=False`、model_calls 未增加。再用小 `max_input` 单独触发 token_budget，记录原因不能混同。
- [ ] 覆盖中文、空工具、长工具定义、工具调用/结果、输出额度不足、取消和超时；计量不得修改输入消息或工具对象。
- [ ] 扩展 `safe_trace_events()`：只接受上面字段的有限类型/枚举，数字范围沿用原约束，指纹只接受 64 位十六进制；未知字段丢弃。明确接收现有 context_budget/token_budget 错误码。
- [ ] 诊断脱敏测试向字段中注入原文、错误类型、任意枚举、非法 hash 和多余键，断言公开事件不含这些值。诊断沿用现有有界事件数量。
- [ ] 重跑 Task 1 及新增拒绝/脱敏测试，应通过，网络 0。

## Task 3：三条实际调用路径接入并落库验证

- [ ] 在 Assistant 的 `reserve()` 点调用适配器，复用返回的 progress，删除原来同次调用重复创建的 started 事件。在紧邻 Provider 调用前设 `provider_called=True`；它表示进入适配器，不保证网络请求已经送达。
- [ ] Dynamic 的每轮 repair 先形成完整 request，再调用适配器；复用已有格式/语义修复标志。预留拒绝的事件随 partial 结果返回。
- [ ] AgentSession 的 decide/extract 与格式修复采用相同处理；保留现有历史裁减、调用次数、响应校验与错误语义。
- [ ] 三条路径各添加“原请求能放下、追加修复说明后超限”的测试。先在原实现证实缺少被拒绝请求诊断，再使测试通过；Provider 只收到允许的那一次，修复拒绝不多调用模型。
- [ ] 使用现有合成定义和 FakeProvider 构造聊天与正式审查的预算拒绝，经过真实 executor/临时 SQLite 的 Run 事务，检查 `run_events.summary_json` 有 context、rejected、`provider_called=False`，业务状态为原 partial 而非 completed。
- [ ] 使用现有 SQLite fixture 覆盖旧记录没有 context 的回读；越权、资料快照改变、旧 attempt 的持久化继续拒绝。没有数据库结构变化，本步不创建迁移。
- [ ] 已知 usage 与未知 usage 断言继续通过：未知不记 0；preflight 拒绝不增加 unknown_usage_calls；请求指纹和业务 payload 指纹分别处理。

定向命令：

```powershell
& E:\developTools\miniconda3\envs\docucheck\python.exe -m pytest backend/tests/test_context_request_usage.py backend/tests/test_context_request_paths.py backend/tests/test_context_request_persistence.py backend/tests/test_agent_policy.py backend/tests/test_assistant_context.py backend/tests/test_layered_context.py backend/tests/test_provider_contract.py -q -p scripts.offline_guard
```

预期：全部通过，网络 0；测试数按实际结果记录，不预填通过数量。

## Task 4：验证、复核和独立本地提交

- [ ] 自查每个进入 Provider 的路径与每个 request-before-reserve 路径，核对格式/语义修复、取消、已知未调用和未知结果；不用新建代理完成本步。
- [ ] 运行 `python.exe -m ruff check backend/app backend/tests` 与项目要求的完整离线门禁，报告使用新的文件名。

```powershell
& E:\developTools\miniconda3\envs\docucheck\python.exe -m scripts.verify_offline --report data/verification/context-request-diagnostics-A1.json
```

若该报告已经存在，先选新的唯一文件名，不覆盖旧证据。没有前端改动，不为此重复前端构建。新增诊断不增加真实模型或服务调用；如出现需要数据库事务实连验证的实际缺口，先记录具体风险，再使用隔离测试库。

- [ ] 新增 `docs/context-request-diagnostics-2026-10-21.md` 记录源码身份、回归/全量、网络 0、真实模型 0、未改变的旧阈值及 A2 待实施范围。更新设计/计划复选框，只勾实际完成内容。
- [ ] 仅暂存本步隔离工作区的文件，检查 staged diff 后本地提交：`fix: record complete context requests before budget rejection`。不推送、不部署，不夹带原资料关联 WIP。

## 后续 A2 的明确输入

A1 的数字诊断将用于核对：旧 16k/48k 拒绝分别发生在哪一步、完整请求主要由什么组成、未知 usage 和匹配分词器的边界。A2 按总体设计统一模型容量和计量，默认累计用量改统计/提醒，保留显式配额与调用/耗时保护，再启用按阈值整理及分批恢复。A1 本身不能被报告为“已经支持 1M 自动压缩”。
