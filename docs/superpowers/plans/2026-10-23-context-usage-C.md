# C 上下文用量入口实施计划

> **For agentic workers:** Use subagent-driven-development for the independent frontend task and independent review. Follow TDD and verify before completion.

**Goal:** 在聊天输入框下方查看最近一次已记录请求的估算用量与历史整理状态。

**Architecture:** 请求事件保存有限记忆数字；只读接口投影当前attempt的最后模型事件；独立Vue组件管理弹层与取消/超时，并由ChatWorkspace传入当前所选对话最近Run。

**Tech Stack:** FastAPI/SQLAlchemy/Pydantic、Vue3/TypeScript、pytest/Vitest。

批准规格见[设计](../specs/2026-10-23-context-usage-C-design.md)。复用现有隔离分支，不改主工作区WIP。

## 1. 后端记录和只读投影（主线程）

文件：`backend/app/agent/context_usage.py`、`assistant.py`、`diagnostics.py`；新增`backend/app/db/context_usage_query.py`、`backend/app/api/context_usage_schemas.py`；路由接入`api/main.py`。

- [x] 先写`backend/tests/test_context_usage_view.py`，通过临时SQLite+真实HTTP断言缺路由为404，加入当前attempt、最后事件、未知/畸形与权限反例。
- [x] 请求准入前按白名单复制`history_turns_selected/semantic_notes_selected/task_handoff_selected`，只接受非负整数和0/1交接值。新可选参数`memory_audit=None`不改变旧调用。
- [x] GET使用同一连接查询Run+Case删除状态，倒序读取当前attempt至多128个agent_model事件，取最后一条而非最大历史占用；按有限数字/状态投影，record无有效计量为null。
- [x] 响应固定归属字段；estimate明确字节估算和配置token不同。usage只接受API返回非负整数，缺失为null。summary_status只由该attempt摘要事件判定。
- [x] 执行`python -m pytest backend/tests/test_context_usage_view.py backend/tests/test_context_request_usage.py backend/tests/test_context_request_persistence.py -q -p scripts.offline_guard`，确认网络0。

接口结构固定为：

```typescript
interface ContextUsageView {
  run_id: string; case_id: string; conversation_id: string; snapshot_id: string
  revision: number; attempt_id: string; run_status: string
  record: null | {
    seq: number; created_at: string; phase: string; status: string; provider_called: boolean | null
    estimate: { input_bytes: number; limit_bytes: number; model_window_tokens: number;
      output_reserved_tokens: number | null; safety_reserved_tokens: number | null }
    usage: null | { input_tokens?: number; output_tokens?: number; total_tokens?: number }
    memory: null | { history_turns: number | null; summary_notes: number | null; task_handoff: boolean | null }
    summary_status: 'completed' | 'failed' | 'not_recorded'
  }
}
```

## 2. 页面组件与聊天接入（独立前端子任务）

新增`frontend/src/components/ContextUsage.vue`与测试；`frontend/src/api.ts`加入上述类型；`ChatWorkspace.vue`按消息顺序选择最近Run，传case/conversation/run/attempt/status；`style.css`追加局部样式。

- [x] 组件props固定`caseId: string|null, conversationId: string|null, runId: string|null, attemptId?: string, runStatus?: string`；缺归属不发GET。
- [x] 测试先红：入口、估算单位、0上限与超限、未知与旧记录、两种摘要状态、关闭/Esc、失败重试、20秒超时、归属不匹配和迟到响应。
- [x] 懒读取，展开才GET；Run/attempt/status变化在打开时重读；关闭/切换/卸载取消旧请求。AbortController+Promise.race覆盖token provider悬挂；不读取整段events到页面。
- [x] 只将字节/同口径输入上限计算百分比；不把实际累计usage用作窗口占比。所有数字有限且非负才能展示；未知显示未记录。
- [x] 桌面360px以内、窄屏视口内、上展内部滚动；可访问名称/aria-expanded、键盘关闭与焦点归还；沿用项目样式。
- [x] 执行`npm test -- src/components/ContextUsage.test.ts src/components/ChatWorkspace.test.ts`后全量`npm test`和`npm run build`，不安装依赖。

## 3. 复核与交付（主线程）

- [x] 独立规格/质量复核，实证缺陷先反例后修复。
- [x] 全仓Ruff、受影响后端、前端全量与构建；如改动风险需要再运行完整后端门禁，绑定唯一报告和源码清单，不重复已足够验证的测试。
- [x] 合成浏览器查看桌面/窄屏、失败/旧记录/超限；不接业务库、不调用真实模型。
- [x] 更新入口与交付记录，注明源码完成与未部署、估算限制、真实模型0；本地提交并保留隔离工作区，核对主工作区WIP不变。

最终源码、测试、浏览器焦点补修、估算限制及未部署状态见[交付记录](../../context-usage-C-2026-10-23.md)。
