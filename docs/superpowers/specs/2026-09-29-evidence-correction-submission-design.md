# M3-11b：来源支持的事实修正提交

日期：2026-09-29。延续已确认的“只修正原文支持的提取事实”设计。本步交付可独立使用的后端提交与审计入口；新结论的确定性重算另作 M3-11c。

## 请求与校验

`GET /api/v1/agent-runs/{run_id}/facts` 只返回经重新核验的 v2 事实集、可用性及当前版本标志。历史 v1 或超限 Run 返回 `available=false`。`POST /api/v1/cases/{case_id}/corrections` 接受 `source_run_id`、`fact_set_id`、`fact_id`、新的 `raw_value/raw_unit`、非空理由和 `expected_revision`，必须带 `Idempotency-Key`。`GET /api/v1/cases/{case_id}/corrections/{correction_id}` 返回追加审计内容，包括旧事实、新候选、证据 ID 映射及理由。

服务端从已完成或部分完成的当前 Run 中查找事实，不接受客户端自填证据 ID、供应商或型号。新数值及单位必须由该事实已引用的原文证据验证；本步只修正值与单位，不改变字段、供应商、文件角色、型号、比较口径或原件。值未变化、找不到事实、跨 Case、旧版本、无法验证的原文和解析未完成均拒绝。Case 有仍活跃的 Run 时拒绝修正，避免修正期间继续发起模型调用。

## 原子提交与版本

锁定 Case 并核对 `expected_revision`。同一事务写入追加式 `corrections` 审计记录、创建 `InputSnapshot(revision+1)`、克隆原快照的不可变文档版本及已解析证据、更新 Case 版本和同键幂等记录。新文档、证据拥有新 ID；原件路径/哈希不变，旧快照与 Run 保留。修正记录保存来源 Run/事实/旧证据、新证据映射、原事实、经验证的新候选值和理由。旧 Run 的 `is_current` 随版本递增变为 false，不能作为当前结论；回执标记 `pending_recheck`。新快照现在只是可读取的原始资料，新事实尚未应用为正式规则结果。API 和 Run repository 在此状态下拒绝新 Run，直到后续规则重算接入。

同键同请求返回首次提交回执；同键不同请求返回 409。并发不同键但同一预期版本只能一条成功。新版本提交后不自动创建 Agent Run，也不调用模型。

## 数据与验收边界

增加 Alembic `0006_corrections`。验收覆盖来源校验、幂等与并发、旧结论失效、旧新证据映射、迁移以及拒绝活跃 Run。复制最多 25,000 个 Agent 可读证据块；超出时拒绝，不在长事务里无限复制。性能和存储增长需另测，M5 的修正规则重算 ≤2 秒仍待 M3-11c 实测。删除清理与前端不在本步。
