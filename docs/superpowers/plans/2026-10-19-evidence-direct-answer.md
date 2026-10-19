# 资料问答直接答案修复计划

> 执行方式：在当前会话按以下清单完成，用户已确认答案与引用同时提交并授权实施，不重复请求确认。

**Goal:** 模型读过工具结果后提交简短直接答案与来源引用，正文不展示内部证据长 ID。

**Architecture:** 在现有 answer_evidence 工具增加必填 answer，保留检索、原文回读、完整语境、权限和范围验证。新答案保存在版本化 evidence_answer 对象中，历史回读校验其检查点摘要；旧记录继续使用原渲染。复用已有来源按钮，不改页面布局、不增加模型轮次。

**Tech Stack:** 现有 Python 3.12、Pydantic、SQLAlchemy、pytest、Vue/TypeScript；不安装依赖。

## 范围与业务断言

- 苹果报价示例应先回答“25 元/箱，不含税”，原文 A8/B8/C8 作为三个可打开来源保留。
- 模型提示要求短答、保留单位/含税/否定/条件，区分供应商和买方。存在冲突时说明双方；资料未给单位时不得猜测。
- answer 与 citations 同一工具响应提交；无引用时由服务端给固定无答案说明，不展示无依据的模型文本。
- 模型自然语言总结不等于确定性审查结论；来源校验不宣称能证明每句总结的语义正确。既有来源、窗口及完整上下文守卫保留，正式审查路径不变。
- 新正文中的已知证据 ID 改成短引用编号，未知长 ID 拒绝。已有文件名/位置按钮继续用于查看原文。
- 保留已有检索降级短提示；本步不修复向量索引运行状态。
- 保留工作区已有资料关联引导修改及其测试和文档；仅提交本次修改。

## 实施与验收

- [x] 新建 backend/tests/test_assistant_direct_answer.py，用脚本 Provider 验证三格价格的直接答案和引用、无依据回答拒绝、未读/伪造引用拒绝、短引用编号、工具必填 answer。先运行并观察旧代码失败。
- [x] 修改 backend/app/agent/assistant.py 与新建 backend/app/agent/assistant_answers.py，实现 answer_evidence(answer, citations)、限长答案与版本化保存及新旧渲染选择。
- [x] 修改 backend/app/db/assistant_executor.py 与 assistant_repository.py，保存并核对 evidence_answer/引用/检索元数据的联合摘要，重新核验来源后渲染；无迁移。新增持久化回读/篡改/旧协议测试。
- [x] 现有离线 Provider 脚本显式提供 answer，旧语境测试同时断言完整引用保留；历史兼容用旧 payload 与检查点模拟，不削弱现有反例。
- [x] 运行相关 pytest（启用 scripts.offline_guard），再运行完整 python -m scripts.verify_offline --report data/verification/evidence-direct-answer-final-2026-10-20.json。后端2115通过/52跳过/网络0；前端222项与类型/构建通过。
- [x] 复核 diff、生成交付记录、本地提交本次变更，保留无关未提交内容。运行服务和真实模型验收单独记录，本轮未更新运行服务或调用真实模型，已在交付记录中说明。

定向命令（Conda docucheck）：

```powershell
& 'E:\developTools\miniconda3\envs\docucheck\python.exe' -m pytest backend/tests/test_assistant_direct_answer.py -q -p scripts.offline_guard
& 'E:\developTools\miniconda3\envs\docucheck\python.exe' -m scripts.verify_offline --report data/verification/evidence-direct-answer-final-2026-10-20.json
```
