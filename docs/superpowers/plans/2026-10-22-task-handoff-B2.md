# B2 同任务跨对话与版本交接实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 同一采购任务的新对话或新资料/清单范围能够读取有原消息依据的历史目标与待办，并明确历史和当前版本边界。

**Architecture:** 沿用已批准轻量设计。增加有界、确定性任务交接记录，复用conversation_summaries的独立task_handoff_v1类型和全局递增version，不增加模型调用或迁移。Assistant只把交接当辅助历史；正式Dynamic不注入。原始消息按已捕获来源白名单回读，不开放任意Case/conversation参数。

**Tech Stack:** Python3.12、SQLAlchemy/MySQL与临时SQLite、现有Assistant和受控工具。

基线f37598c；只在现有隔离分支实现、本地提交。不合并、推送或部署，不改前端、真实.env、业务库或调用真实模型。复用B1守卫和原phase核验；不继承候选清单、旧事实、旧结论或执行许可。没有历史时保持现有行为。

## 契约与验收

- 当前Case/Run/snapshot/清单/资料关联范围来自服务端；只选同Case、当前问题之前、不同对话或不同业务范围的最新完整终态phase。不同任务和工作区采购事实不共享。
- 冻结交接记录只引用当时可见来源，显式retry读取同Run原记录，不追入未来对话。保存持Case锁及attempt/fence/成员权限/取消删除守卫；读取重验来源、摘要和记录指纹，旧worker不能覆盖新记录。
- 短历史带有界完整原始目标/最近请求和状态位置，不自动带旧答案；旧问题超限只保留回查位置，不截成“完整目标”。存在有效B1摘要时仅选goal/open_question/next_step，decision不跨范围自动继承。所有内容明确为历史讨论，待办未自动视为当前待办。
- 交接正文最多3500 UTF8字节，来源数量有界；以完整可选单元裁减。当前范围指针、来源范围与变化字段保留。无法容纳时省略整个交接，不能裁当前完整问题/规则或制造超限。
- 新read_task_history工具只接受HistoryArgs(message_ids/part/start_char)，来源白名单来自服务端交接记录；完整分页回读仍核对Case、原conversation/snapshot、phase和访问权限。旧答复不是当前报价证据或正式结论依据。
- 摘要和交接互不替代，B1压缩前后保持交接不扩张；日期直答、可信正式重试与正式Dynamic不使用交接触发新的业务操作。
- context_manifest只含ID/版本/指纹/有限状态与回读位置，不含历史正文。原话、旧摘要、旧Run、候选和报告不改写；两类已有摘要仍独立。

## Task 1: 有界来源与不可变交接仓储

Files: 新建backend/app/db/task_handoff_repository.py、backend/tests/test_task_handoff_repository.py；必要的公共辅助只在确有需求时局部抽出。

- [x] 先写SQLite回归，运行观察缺少接口/行为的失败：新对话短历史、同对话新快照/规则范围、不同Case拒绝、未来phase不进入、空历史、源记录/摘要篡改、成员撤销/取消/删除/attempt/fence、同Run重复捕获、旧摘要kind隔离。
- [x] 实现接口：

```python
class TaskHandoffRepository:
    def prepare(self, claim): ...  # 冻结并返回context/manifest/input_digest
    def validate(self, claim, view): ...
    def read_history(self, claim, view, message_ids, *, part="both", start_char=0): ...
```

- [x] 真实临时SQLite验证prepare→来源重验→白名单分页回读，篡改和越权先红后绿。时间锁顺序另做SQLite合同模拟；补充临时MySQL8.0.41/RR精度及并发prepare实连，明确不等于标称8.4实测。

## Task 2: Assistant上下文与受控回读接入

Files: 修改backend/app/agent/assistant.py、assistant_context.py及backend/app/db/assistant_executor.py；新增backend/tests/test_task_handoff_context.py、test_task_handoff_workflow.py。

- [x] 先写真实API→worker脚本Provider回归：新对话看到历史目标和当前指针；旧资料/旧清单标为历史；当前确认值与旧讨论999保持隔离；无候选自动保存；未授权消息拒绝；正式流程不注入；上下文变紧先省略交接。
- [x] 接入独立task_handoff层和task_history_reader，注册受控工具：

```python
observation = {"historical_context": task_history_reader(query.message_ids, part=query.part, start_char=query.start_char),
               "notice": "历史讨论仅供回忆，不确认当前规则或事实。"}
```

- [x] 与B1、旧历史/偏好/清单问答和正式retry相关回归一并验证，不新增模型调用，不改前端。

## Task 3: 复核与交付

- [x] 独立规格复核→修复反例→质量复核，来源、权限、版本和预算守卫为重点。
- [x] Ruff、包含新增文件的暂存diff检查、文档链接检查，冻结源码清单后运行完整离线门禁。

```powershell
& E:\developTools\miniconda3\envs\docucheck\python.exe -m scripts.verify_offline --report E:\PycharmProjects\docucheck\data\verification\task-handoff-B2-20261022-01.json
```

- [x] 更新交付记录及入口状态，本地独立提交并绑定报告/源码指纹；保留主工作区六份旧WIP和运行服务。前端C继续先讨论。
