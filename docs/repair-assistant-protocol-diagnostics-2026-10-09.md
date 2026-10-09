# 助手协议失败诊断补充

真实浏览器资料问答首次出现 `partial / invalid_output`，1 次调用、2639 输入及 114 输出 token，已知用量完整。模型响应未满足单工具协议，尚未执行工具。原审计在本地协议检查之前把事件置为 completed，因此无法区分零工具和多工具。失败 attempt 已保留；页面显式重新处理后，第二 attempt 用 2 次模型调用完成原文查询，不覆盖失败记录。

本步调整事件状态顺序：先记录 usage，保持 started，通过 schema、恰好一个工具及 64KB 输出上限后才 completed。失败由原 catch 改为 failed，只记录已有有限枚举 `missing_tool_call`、`multiple_tool_calls`、`tool_schema` 或 `output_limit`，不保存模型原始响应或工具参数。已知用量不丢失。不放宽校验或新增自动重试，也不能据此承诺真实模型不会再违反协议。

两项新回归先 RED（事件错误地为 completed），修复后 agent/audit/workflow/stages 共 61 passed，diagnostics 6 passed，Ruff 通过。独立复审 agent 14 passed，并内存检查超大输出/schema 失败；未发现新问题。完整 1341/41/网络0 门禁对应此前引用修复代码，本微小补充另以最终 67 项回归覆盖。

资料问答重试成功和跨轮“这家供应商”的交期出处查询均已完成；完整在线验收结论与服务应用记录见后续报告。本步源码先独立提交，再同步服务。
