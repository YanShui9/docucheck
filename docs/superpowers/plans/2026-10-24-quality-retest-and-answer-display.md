# 业务复测与答复展示执行计划

> 按已确认的两份规范逐项执行；实施阶段使用 executing-plans 与 test-driven-development，交付使用 verification-before-completion。复测与展示独立记账，执行中不重复请求已获确认。

**目标：** 完成当前版本旧12包及新6包业务测量，并在保持原始结果的前提下精简聊天答复与来源显示。

**结构：** 复测使用已部署固定后端及现有 `evals.dynamic` HTTP适配，原始资料/gold/回执在新证据目录。界面只增加纯展示函数及独立答复组件，不修改后端判定与历史持久化。

**技术：** Python/httpx、现有MySQL只读核对、Vue/TypeScript、Vitest。

## 1. 固定复测身份

- [x] 保存Git、运行源码、数据库/原件/WIP基线及已知模型账本；健康、鉴权和解析入口检查。
- [x] 校验旧12包/104位置/58原件；制作并校验新6包及其先定答案，分别冻结索引哈希。
- [x] 用 `evals.dynamic.run.prepare_protocol` 生成协议，在发送前保存所有任务及未开始状态。
- [x] 串行 `run_pack`，每个任务独占日志/JSON/XLSX，结果未知停止；统计实际用量并延续跨任务预警。
- [x] `score_pack`、`aggregate_scores` 分组评分并核对原始回执，保留失败和保守策略差异。

## 2. 答复展示回归与实现

**文件：** 新建 `frontend/src/answerDisplay.ts`、`frontend/src/answerDisplay.test.ts`、`frontend/src/components/AssistantAnswer.vue`、组件测试；修改 `ChatWorkspace.vue` 及受影响测试、`style.css`。

- [x] 先写失败测试：旧格式正式结果保留每项结论、提取完整来源并原ID跳转；普通包含来源字样的文本不截断；非格式化/残缺尾段全文保留。
- [x] 纯函数输出 `{ text, sources, original }`，仅对明确结构作拆分；相同ID去重但不按文件名合并不同证据。
- [x] 组件使用当前短答和结构化citations；显示前三条来源，其余放原生details；整理过的答复提供原文details。
- [x] 接入现有 `answer()` 与 `cite()`，沿用当前结果失效/权限/修正中守卫。
- [x] 红绿验证后执行完整前端测试及类型/构建：`npm test`、`npm run build`。若Junction限制阻止测试，复用已有仅放行本机既有依赖的ignored runner，不修改产品配置。
- [x] 桌面与窄屏检查当前短答、正式结果、展开、来源跳转与刷新；实际 partial 历史完整报告核对 3/4 批与 10 格非正式，partial 答复整理分支由组件测试/复审覆盖；核对 API/导出保持原值。

## 3. 收尾

- [x] 业务路径复审，重要问题须有具体反例；必要时按 requesting-code-review 获取独立审查。
- [x] 核对原数据/WIP保留，报告两组业务结果、用量、展示验证及未验项，更新入口文档。
- [x] 文档 profile、`git diff --check` 及公开文件检查通过，交付记录随本次文档提交保存。GitHub 同步沿用用户已授权范围，在本地提交后执行，远端状态以默认分支与对应 CI 为准。

规范：[业务复测](../specs/2026-10-24-current-quality-retest-design.md)、[答复展示](../specs/2026-10-24-answer-source-readability-design.md)。

## 本轮验收记录

业务复测旧组 7/12 completed、状态 44/104、严格 1/12；新组 5/6 completed、16/24、严格 1/6。全部 partial 及展示观测器兼容问题保留，质量出口未过。模型请求 58 次、未知 0，无人工重试。详见[业务复测](../../current-quality-retest-2026-10-24.md)。

展示源码提交 e1fb25c，324 项/20 文件与类型/构建通过，两个 P2 红测后关闭。真实页面覆盖三份正式历史、原文入口、桌面/窄屏与刷新；partial 运行只读回放及组件分支的覆盖范围见[展示交付](../../answer-source-readability-2026-10-24.md)。保留原 654 份原件、既有业务行及主区六份 WIP；本轮未实现报告中列出的下一步后端修复。
