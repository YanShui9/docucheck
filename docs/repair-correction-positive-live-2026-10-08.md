# 同源事实修正正向验收与完成度说明修复

日期：2026-10-08。基线：`7f719d5`。本步补齐浏览器正向修正，修复实测发现的完成度说明错误。真实大模型请求 **0**，没有启动、重启或修改Docker服务及前端样式。

## 验收资料与范围

在既有合成验收工作区，通过真实OIDC页面新建独立Case `c4fbfe1f45814d0db5cfbec8b4de6365`。随后使用本机应用仓储、解析器、执行器准备测试夹具：买方要求“苹果 数量不低于10箱”，报价原文“苹果 数量12箱或8箱”，初始抽取事实只有12箱。资料与初始清单确认由测试脚本准备，不冒充用户通过页面上传/确认，也不作为真实采购结论或抽取准确率证明。

初始报告由 `ScriptProvider` 子类通过正常执行器生成，不使用配置中的真实Provider。两次测试Run各3个模拟Provider方法调用，共6次；模拟token不计作真实模型用量。每次Run在提交事务内将自身Outbox类型设为 `test.correction.*`，与运行中的真实dispatcher隔离，未将夹具送到付费模型。

报价中的“或”使初始业务结果为“待人工确认”；完整处理与业务需要确认是两个独立状态。初始正式标记只表示当前版本的完整处理结果，不代表报价满足要求。

## 真实页面及MySQL结果

1. 浏览器选择12箱事实，提交由相同原文支持的8箱和修正原因；API实际保存成功，保留12箱，追加8箱。
2. 页面刷新后显示“资料冲突 · 非正式”“需人工复核”，审计展示12箱→8箱及原因；原文面板定位 `correction-synthetic-quote.xlsx / 修正验收!A1`。
3. 首次成功Run `c58e76be6ded486a9a4817629f637be7`暴露错误：覆盖1/1、处理complete，但聊天错误提示“部分完成，未覆盖项仍为处理未完成”。该历史Run和审计保留，未回写伪装成修复后结果。
4. 修复后使用相同合成资料的独立新会话，初始报告重新由测试Provider准备；Run `eb334fa9ebe6415f8a17ff586f0dc789`通过真实OIDC页面再次提交8箱。
5. 本次聊天正确说明“指标审查完成”“结论仍需人工复核”。MySQL facts保留12/8、审计1条、`needs_review=true`、coverage完整、processing complete、business资料冲突、`is_formal=false`。
6. 浏览器下载Excel、JSON及已验证原件：导出与数据库检查项、来源及非正式标记一致，原件SHA-256匹配。assistant envelope、持久化聊天答复与v5记忆检查点digest一致。原苹果审查Case及既有waiting_user任务未被本步修改。

## 修复与回归

根因：`DynamicRepository.correction()`调用 `render_review(... complete=False, needs_review=True)`，把人工复核错误地等同于抽取未完成。改为按已验证payload的供应商coverage生成完成度说明；`needs_review`、事实追加、原值保留、审计、非正式标记及事务锁均保持原规则。

- 修复前新增断言复现：两项失败、一项通过；失败分别覆盖动态核验与assistant持久化答复。
- 增加“第二供应商抽取失败后提交同源修正”边界测试：仍保留partial、未覆盖警告、人工复核说明和非正式结果；修复前即通过，防止修正文案时掩盖真实未完成。
- 修复后定向：`test_dynamic_workflow.py`、`test_assistant_workflow.py`、`test_dynamic_report_types.py`共 **31 passed / 网络0**。
- 最终完整门禁：`python scripts/verify_offline.py --report data/verification/correction-positive-live-2026-10-08/offline-final.json`，**1269 passed / 41 skipped / 网络0**，265.90秒；Ruff、pip check通过。
- 前端源代码未改，本步未重复前端测试/构建，上步148项成绩不记为本步新成绩。

## 运行应用及误操作记录

本机API已重启并使用修复后代码，当前PID为20404。正确启动入口为 `scripts/start-api-oidc.ps1`，它提供OIDC参数并加载根.env。首次误用裸uvicorn命令，漏带原进程OIDC环境，短暂启动成local兼容模式，页面返回 `oidc_required`；随即停止该进程并使用上述脚本重启。**不能声称本次重启期间认证配置始终不变**。恢复后健康/live、ready均200，匿名/me和报告checks均401；最终浏览器修正和导出均在恢复后的OIDC模式完成。

现有worker镜像未重建，118个Python文件中仅 `db/dynamic_repository.py` 与宿主不同，本次改动方法只有API修正接口调用；不声称所有容器源码同步。后续统一镜像部署需带入此修复。原worker只读CA及其他容器保留。

## 交付与剩余事项

脱敏摘要：[JSON证据](repair-correction-positive-live-evidence-2026-10-08.json)。原始夹具、payload、审计、完整离线门禁报告、运行信息及截图保存在被忽略的 `data/verification/correction-positive-live-2026-10-08/`，摘要记录哈希。合成任务保留以便用户查看，当前浏览器展示修复后的冲突报告；没有删除用户资料。

浏览器正向修正现已补齐，产品链路清单可结合前几步证据勾选。**BGE-M3/Milvus实连复验尚未进行**。本次夹具不证明真实模型会完整提取同一原文所有备选，也不证明多品类准确率、生产容量或高可用。部署回滚可回退本步Git提交并通过OIDC脚本重启API；已存在的审计资料保持追溯，不直接删除或回写历史报告。
