# 审查进度只统计当前已验证批次

合成资料正式审查在供应商schema修复后完成，但真实页面报告显示“已核验3/2”。同一Run保留了旧规则版本的一个缓存批次和当前v3两个批次；`progress()`原来统计整个Run的SQL缓存行数，分母却来自当前manifest。

改为统计经过 `verified()`、原文和manifest核验的当前 `batch_coverage` 中 `complete=True` 的唯一批次。旧SQL缓存行仍保留，不计入当前报告；无payload返回0/0，不对排队或抽取前失败宣称完成。

独立复核发现“抽取前停止”空manifest特殊分支可以接受不在计划中的batch声明，现同时要求 `batch_coverage` 为空。既无计划又声称完成或未完成的异常声明均拒绝，不通过数据库重算digest规避验证。

验证：v1/v2同Run升级重试先RED，当前进度实际3/2；修复后旧partial为1/2、当前completed为2/2。额外空manifest异常声明False/True先RED，HTTP200；补守卫后拒绝409。最终workflow、batch_retry、run_binding三模块 **32 passed / 55.11秒**，全backend Ruff、diff检查通过。独立复核另跑4项通过（10.99秒），并直接验证正常抽取前空声明仍可重放。服务应用见[真实验收应用记录](repair-assistant-duration-applied-2026-10-10.md)。

本次仅修复进度和验证边界，不改变事实、规则版本、已确认清单、原文关联或正式业务判定。页面“处理完成”表示处理与覆盖完成，采购数量/交期仍因品目对应不明确而需要人工确认；不能把它解释成供应商满足全部采购要求。
