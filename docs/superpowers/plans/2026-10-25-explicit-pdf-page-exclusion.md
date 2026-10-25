# PDF 页面显式排除实施计划

> **For agentic workers:** Use executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 用户显式确认其他 SKU 的整页 PDF 排除；完整覆盖可接受有效排除页，历史与原件保持。

**Architecture:** 增加独立排除值对象和确定性校验，schema 2 在既有不可变关联 JSON 内冻结排除范围。v16 使用该范围，旧规则不接受 schema 2。弹窗沿用当前样式；排除说明贯穿报告与两类导出。

**Tech Stack:** Python/Pydantic/SQLAlchemy、Vue/TypeScript、pytest/Vitest、MySQL。

设计依据：[已确认方案](../specs/2026-10-25-explicit-pdf-page-exclusion-design.md)。执行工作区为现有 `codex/context-request-diagnostics`，禁止触碰主区六份 WIP。

## 1. 确定性来源合同与版本

文件：新建 `backend/app/criteria/page_exclusions.py`、`backend/tests/test_page_exclusions.py`；修改 `item_bindings.py`、`facts.py`、`money_sources.py`、`agent/dynamic.py`。

- [ ] 构造含两个 SKU 页的真实 EvidenceRepository，先验证 schema 2 合法排除后 `review_plan(..., rule_version="dynamic-rules-v16")` 来源完整；当前版本应失败。
- [ ] 补反例：目标 SKU、混合 SKU、无可靠买方 SKU、同页已关联/锚点、跨文件锚点、共同条款、partial/未处理页、伪造指纹、重复页及容量。
- [ ] 新增 `PdfPageExclusion`，字段 `document_id/source_sha256/parser_version/page/other_sku/sku_anchor/reason`；`validate_exclusions()` 只从服务端原文生成 `supplier/evidence_refs/source_fingerprint/anchor_fingerprint`。
- [ ] `ValidatedBindings` 增加不可变 exclusions，schema 1 payload 不添加字段；schema 2 重新校验全部来源。新默认 v16 继承所有 v15 分支，显式保留 v15 常量集合。
- [ ] 仅 v16 将已验证排除 refs 计入库存；任何旧规则+schema 2 均拒绝。抽取与 facts 仍仅使用已关联 refs。
- [ ] 运行定向并记录红/绿及源码身份。

核心断言：

```python
assert review_plan(repo, definition, rule_version="dynamic-rules-v16", item_bindings=frozen)[1] == {"A": {"phone": True}}
with pytest.raises(ValueError):
    review_plan(repo, definition, rule_version="dynamic-rules-v15", item_bindings=frozen)
```

## 2. 不可变保存与回读

文件：`backend/app/api/item_bindings.py`、`backend/app/db/item_binding_repository.py`；新增 `backend/tests/test_page_exclusion_repository.py`，复用 SQLite fixture；MySQL 受影响测试单列。

- [ ] 失败测试先覆盖 PUT/GET、旧 schema 1 重放、新 schema 2 历史、幂等/清空排除/版本冲突。
- [ ] PUT 可接收 `exclusions=[]`，未提供时仅对已有 schema 1 保持原请求身份；有排除的当前版本必须显式携带完整列表，防止旧客户端静默取消。更改/取消排除沿原确认事务保存新版本。
- [ ] `_repository()` 加载关联与排除文件并校验全部排除来源；payload schema 2 严格重验，不在 GET 中补写。
- [ ] `continue_current()` 不续用有排除项的版本；既有 schema 1 续用保持。
- [ ] actor、Case锁、active Run、删除/版本/快照/幂等与容量限制覆盖排除。schema 2 的 PUT要求明确 `exclusions_confirmed=True`，取消最后一项也需该字段。
- [ ] 运行关联/API/Run/continuity 相关回归。

## 3. 报告与导出

文件：`backend/app/api/run_export.py`、`backend/app/agent/dynamic.py`、`frontend/src/components/ReviewPanel.vue`、相关测试。

- [ ] 先写断言：新 JSON 与 Excel 含排除文件/页码/型号/原因/确认信息；旧导出不新增假记录。
- [ ] API 基于 Run 冻结关联重验后提供排除说明；报告显示“在已确认范围内完成”，展开可核对排除原文与确认人/时间。
- [ ] Excel 独立“范围排除”工作表；JSON 使用 `item_attribution.exclusions`，所有来源从 repo 读取而非客户端拼造。
- [ ] 证明新范围不改变旧 v15 Run/导出与缓存身份。

## 4. 现有弹窗里的显式操作

文件：`frontend/src/itemBindings.ts`、`itemBindingForm.ts`、`components/DocumentItemBindings.vue`；新建 `components/PdfPageExclusions.vue` 及测试以保持组件边界。

- [ ] 先写交互测试：次级入口、单页原文/锚点/原因、加入与取消、最后确认、只读/忙碌、未知保存原幂等重试与跨文档保留。
- [ ] 子组件负责排除草稿和列表，通过 `v-model` 传完整排除请求数组；父弹窗负责全局保存/权限/未保存确认/版本。
- [ ] 禁止前端分页尚未完整时将一部分原文冒充整页；服务器始终独立展开复核。未保存的子表单计入父离开保护。
- [ ] 历史排除仅只读，当前已确认、本次改动、其他文件排除数量清晰；确认框明确含排除页与共同条款声明。
- [ ] 运行 Vitest、vue-tsc 和 build；无登录的合成浏览器验收明确标注测试环境。

## 5. 复审与冻结门禁

- [ ] requesting-code-review 独立复核范围/版本/来源/前端保存；实证问题先红测再修复。
- [ ] 源码冻结后 `python -m scripts.verify_offline --report <新文件>`；网络 0、全部受测文件前后哈希一致。前端全量测试/类型/构建。
- [ ] 在临时独立 MySQL 实跑受影响事务/回读/并发，不能接业务库跑测试。
- [ ] 提交产品代码，记录精确 SHA；主区六 WIP、既有原件与历史保持。

## 6. 固定部署与有限验收

- [ ] 说明并固定提交部署；备份、worker/API/HTTP 前端源码核对、健康/匿名鉴权；备份不冒称恢复验证。
- [ ] d03 核对后仅追加排除确认版本并新建一次正式审查；保留失败、自动修复与全部调用。当前预警轮已知 58 次起继续累计。
- [ ] API/SQL/JSON/XLSX 对照，旧 Run/旧关联版本/原件哈希不变，状态按真实输出记录。原 18 包评分不替换。
- [ ] 受保护 UI 等用户登录后独立验收；不绕过登录。其余后台收尾继续。
- [ ] 更新交付与入口文档、本地提交和正常快进推送两分支、核对精确提交 CI/GitHub 展示；记录完成项及页面未验收边界。

执行状态由本文件复选框和对应证据共同说明，未打勾不计作通过。
