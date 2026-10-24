# 金额识别与独立税口径来源实施计划

> **For agentic workers:** 使用 subagent-driven-development 按下列任务实施；任务完成后先规格复核，再质量复核。

**Goal:** 识别明确的不含税单价，保留独立税说明的原文与适用范围，避免本例因 25≤25 而忽略含税冲突。

**Architecture:** 新规则 `dynamic-rules-v13` 继承 v12 的全部守卫。价格主引用保持原合同，服务端从已确认关联范围收集独立税说明，重新验证并绑定完整事实身份；模型不能通过省略税说明消除冲突。关系无法证明时保留人工确认，旧版本按旧协议重验。

**Tech Stack:** Python/Pydantic、现有 EvidenceRepository/ReviewBindings、LangGraph 动态审查、SQLAlchemy、pytest/offline_guard。

用户已确认[诊断与方案](../../formal-review-diagnosis-2026-10-23.md)第5节。本计划在既有隔离分支执行，不修改主工作区旧WIP，不修改前端或真实清单，不新增自动模型重试，不自动部署。

## Task 1: 新版金额来源校验与确定性结论

Files:

- 修改 `backend/app/criteria/facts.py`：版本集合、新金额来源元数据、重验、来源汇总与结果。
- 修改 `backend/app/criteria/validation.py`：仅新版本启用有限不含税别称。
- 新增 `backend/app/criteria/money_sources.py`：独立税说明的范围收集及适用关系验证。
- 新增 `backend/tests/test_money_sources.py` 或 `test_money_basis_v13.py`。

- [ ] 先补反例，实跑失败：不含税25、26；主价格行与独立含税行；模型未选择税行仍不能漏冲突。
- [x] 新版本集合显式保留 v12，并继承 presence 肯定事实待人工确认等 v12 语义。
- [x] 价格主引用继续使用同排连续格/同段合同；独立税说明逐组 `source_span` 验证。
- [x] 服务端来源元数据不进入模型候选 schema；`revalidate_fact` 重算并比较，删除或篡改元数据拒绝。
- [x] 同供应商、同文件版本、同已确认品目范围只是必要条件；仅有唯一价格行、相同工作表且归属可验证时组合判断，多价格/归属不清保留人工确认。
- [x] 保留未知或歧义税说明，不擅自转为“没有说明”；预算、总价、参考价仍拒绝。
- [x] 验证 v12 原行为未变；新版本冲突给出两处来源，明确口径25满足、26不满足。

运行：

```powershell
& E:\developTools\miniconda3\envs\docucheck\python.exe -m pytest backend/tests/test_money_sources.py -q -p scripts.offline_guard
```

若实际测试文件命名为 `test_money_basis_v13.py`，命令对应替换该文件。预期先出现来源/别称缺失反例失败，再全部通过，网络尝试0。

## Task 2: 动态抽取、读取与历史缓存接入

Files:

- 修改 `backend/app/agent/dynamic.py`：v13继承已有抽取协议，金额独立来源读入计量，修复/缓存重验保持完整性。
- 必要时修改 `backend/app/db/dynamic_repository.py`：仅服务端可重建元数据，不放宽原模型候选批次范围。
- 修改 `backend/app/agent/diagnostics.py`：新增有限错误白名单。
- 新增 `backend/tests/test_money_agent.py`、`backend/tests/test_money_repository.py`。

- [x] 脚本 Provider 返回仅价格主行，正式处理仍保留独立税说明，完成状态与业务冲突状态分别断言。
- [x] 对动态流程执行价格25/26、冲突、未知适用关系、多个价格、不同供应商/品目、跨批次来源反例。
- [x] 模型候选仍只允许本批引用；服务器派生税来源在已冻结完整关联范围内读取、计量并在重验时重建。
- [x] 缓存只保存原候选，不信任缓存中的派生字段；缓存复用、报告回放、人工修正均重新验证税说明。
- [x] 冲突不能因删引用、替换主行或语义修复消失；旧材料来源同批限制保持。
- [x] 覆盖 API→worker→持久化→verified 回读；删除/修改税来源或文件身份时拒绝历史伪造。

运行新增测试及 `test_material_agent.py`、`test_material_repository.py`、`test_dynamic_agent.py`、`test_dynamic_workflow.py` 的存在文件；预期通过、网络0。

## Task 3: API与导出来源完整性

Files:

- 修改 `backend/app/api/schemas.py`：可选税来源分组，旧响应不新增空字段。
- 修改 `backend/app/api/run_export.py`：Excel 独立税来源组，JSON保留完整来源。
- 必要时修改现有后端审查正文渲染模块，沿用现有显示样式。
- 新增 `backend/tests/test_money_response.py`。

- [x] 来源字段通过响应过滤，不带无关范围或用户凭据。
- [x] JSON 与 XLSX 的主价格来源、税说明来源一致，不能把两行伪装为同段原文。
- [x] 旧报告不出现新空字段，旧原件导出行为不变。
- [x] 中文原因明确指出税口径冲突/归属不明，不能把来源校验失败写成已完成的业务结论。

## Task 4: 两阶段复核与交付

- [x] 规格复核逐条核对批准范围，修复实证缺口后再质量复核。
- [x] 执行相关测试、全仓 Ruff、`git diff --check`。
- [x] 冻结源码清单到主工作区忽略证据目录，运行完整 `scripts.verify_offline`，保留唯一报告。
- [x] 若改动事务/锁，增加隔离 MySQL 实连；仅纯事实协议及既有SQLite仓储验证时如实说明未改事务、外部跳过边界。
- [x] 更新交付文档及入口状态，写明本轮模型调用数量、源码身份、未部署与真实报价复测尚待执行。
- [x] 核对主工作区HEAD和6份旧WIP哈希，本地独立提交；不合并、不推送。

完整门禁：

```powershell
& E:\developTools\miniconda3\envs\docucheck\python.exe -m scripts.verify_offline --report E:\PycharmProjects\docucheck\data\verification\money-tax-v13-20261023\offline-01.json
```

验收命令必须对应冻结源码；运行中如再改代码，另建清单和报告，不覆盖旧证据。离线通过不作为真实模型抽取或原失败Run已关闭的证据。

## 2026-10-24验收回填

实现、规格复审、质量复审、相关回归、冻结源码完整离线门禁及本地保存已完成，详见[交付记录](../../money-tax-sources-2026-10-24.md)。本计划最早的‘先补反例’项保留未勾选：初始补丁来自中断对话，本轮没有重建其最初先红后绿的执行顺序，不能补写历史。现有25/26、税冲突及来源遗漏反例已在最终版本通过；本轮新发现的跨关联条目遗漏价格有实跑4失败/2通过→6通过的红绿证据。

Task 2的‘读取、计量’以实际实现为准：派生来源执行既有授权/取消/快照检查，新增税/价格组数上限；没有新增字节计量机制。外部集成跳过、SQLite仓储验证及未部署/真实模型0次边界见交付记录。
