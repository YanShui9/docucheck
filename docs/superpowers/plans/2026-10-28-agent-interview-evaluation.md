# Agent 面试展示评测集实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 用户已授权调研合适后执行，不再询问常规执行方式。

**Goal:** 交付40题采购Agent数据集、可信评分器、真实系统运行和可核查展示报告，之后再教学。

**Architecture:** 新建evals/agent_interview，复用动态资料工具和合法HTTP接口；任务/参考答案与运行器分离，评分器不调用产品判定规则。历史分数及业务数据保持，产品修复不隐含加入评测任务。

**Tech Stack:** Python3.12、Pydantic2、pytest、openpyxl、现有PDF生成依赖、现有HTTP/OIDC/MySQL/worker/检索栈，不新增外部托管依赖。

## Task 1：任务、合同、原件与gold

Files: create `evals/agent_interview/__init__.py`, `contracts.py`, `dataset.json`, `corpus.py`, `README.md`; test `backend/tests/test_agent_interview_corpus.py`。

- [ ] 写失败测试：40个不同task_id；各类及split数量与设计一致；12问8有答案4无答案；开发/测量无共享采购资料组；必要锚点实际存在；参考答案不从产品规则生成；路径不能逃逸输出目录。
- [ ] 运行targeted profile保存红测，然后实现严格合同和资料构建器。
- [ ] 每题包含prompt、fixture/group、category/split、预期kind、关键事实/禁止断言、必要证据组、预期状态/不可变状态、出处与风险；分别保存输入和gold，运行器不得传gold给产品。
- [ ] 实际生成全新PDF/XLSX，核对中文、全文、页码/单元格和SHA。所有输入与gold可公开。
- [ ] 规格复审通过后质量复审，修复后定向绿色并本地提交。

固定数量断言示例：

```python
assert len(dataset.tasks) == 40
assert Counter(t.split for t in dataset.tasks) == {"development": 10, "measurement": 30}
assert not development_groups & measurement_groups
```

## Task 2：评分、参考解和报告

Files: create `evals/agent_interview/grading.py`, `report.py`, `reference_observations.json`; test `backend/tests/test_agent_interview_grading.py`。

- [ ] 用独立参考observation写正例与变异反例。变异包括错单位/否定/税口径、串供应商、丢必要组、无依据写入、partial假正式、重复引用、漏任务和重复trial。
- [ ] 保存红测，实现 `grade_task(task, observation)` 和 `aggregate(dataset, observations)`，不使用产品规则生成答案。
- [ ] strict success必须全部硬断言和必要语义项通过；pending_review/blocked/未执行保留主分母；逐维诊断独立，不能用较高局部分替代任务通过。
- [ ] 30题主表与10题开发表、工程6场景和6题重复各自分开；报告首轮Wilson区间及小样本限制、错误分类和用量未知。
- [ ] 验证每题参考解能够通过；反例确实被拒。规格→质量复审，绿色后本地提交。

计分约束示例：

```python
result = grade_task(task, corrupted_supplier_observation)
assert result.strict_success is False
assert "scope" in result.failed_dimensions
```

## Task 3：真实执行与恢复

Files: create `evals/agent_interview/run.py`, `http_adapter.py`, `journal.py`; test `backend/tests/test_agent_interview_run.py`；按实际接口复用 `evals/dynamic/run.py`已验证的上传/确认/原文方法，不更改旧评测行为。

- [ ] 写无send不会联网、凭据不落盘、请求前写journal、未知写不重发、错误回执不当成功、超时仍在运行则停止后续发送的红测。
- [ ] 默认preview仅列任务/前置/估算，无模型；显式send按新case、实际上传/解析/绑定、对话消息、Run轮询、checks/来源/导出收集执行。
- [ ] 每个提交携带稳定幂等身份；journal独占新文件。结果未知先只读核对；只有明确未发送或已有回执恢复，不静默重发。
- [ ] 多轮按固定脚本，配置准备与Agent任务区分；第二合法用户权限场景及故障控制场景独立适配，不影响原业务索引/队列。
- [ ] 冻结数据/gold/评测/应用版本；运行前对账预警轮，按既有100次预警约定执行。
- [ ] 规格→质量复审，执行targeted与完整离线门禁，身份冻结不变后本地提交。

## Task 4：真实测量和展示收尾

Files: create `docs/agent-interview-evaluation-2026-10-28.md`, `evals/reports/agent-interview-2026-10-28.json`；update `README.md`, `docs/README.md`, `docs/development-roadmap.md`, `CLAUDE.md`。

- [ ] 核对实际运行提交/健康/鉴权/解析/检索；通过合法Owned OIDC客户端运行新合成工作区，不绕过受保护页面登录。
- [ ] 先开发10题查评测器，再测量30题首轮和预定6题额外12trial；首轮记录不可被重试覆盖。6工程场景单列。
- [ ] 保存每题输入/原始响应/来源/最终状态/导出/用量；评分器错误只读重算保留旧观察；产品缺陷先保留，不以无限修复拖延或涂改成绩。
- [ ] 审核原业务库/原件/旧报告和主WIP保持；报告所有未执行/blocked，全部真实完成前不称完整实测完成。
- [ ] docs profile、链接/diff/凭据检查；本地提交、按既有授权正常推送两分支，检查精确SHA CI和GitHub实际展示。
- [ ] 最终交付真实成绩、失败与边界及学习入口。用户未开始教学前不提前展开课程。

## 执行命令约定

Python固定 `E:\developTools\miniconda3\envs\docucheck\python.exe`；开发目录固定为当前隔离worktree。所有验证报告使用ignored目录 `E:\PycharmProjects\docucheck\data\verification\agent-interview-20261028` 的新文件名。

```powershell
python.exe -X utf8 -m scripts.dev_workflow check --profile targeted --tests backend/tests/test_agent_interview_corpus.py --report data/verification/agent-interview-corpus-01.json
python.exe -X utf8 -m scripts.verify_offline --report data/verification/agent-interview-offline-01.json
```

先实际红测再实现；计划中的命令和文件不是已运行证据。超时、未完成与测试失败都保留，不覆盖旧报告。
