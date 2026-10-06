# 动态审查清单：三个大步骤

**Goal:** 让任务的审查指标由用户要求决定，支持手动设置及需求/订单候选识别，并保留原设备八字段历史结果。

**Architecture:** MySQL 保存可修改草稿、不可变确认版本和有身份/来源/理由的修改记录。任意名称使用稳定 criterion_id / item_id；有限规则契约由服务端验证，模型只能提出候选。确认清单、采购事实、资料快照及规则版本分别绑定。现有八字段路径通过显式兼容保留，不偷偷把旧结果解释为新清单结果。

**Tech Stack:** 已有 SQLAlchemy Core / Alembic / MySQL / FastAPI / Pydantic / LangGraph / Provider。无需新依赖。

沿用用户已批准的 [动态指标机制设计](../specs/2026-10-03-context-memory-vector-criteria.md#7-用户设置与自动识别指标)。按用户要求分三个大步骤、每步测试审查后报告，不拆成几十次确认。不修改前端，原实施阶段未操作另一个 AI 负责的检索部署；用户通知完成后已接收报告并联调现有模型/服务。不提交 Git。

产品面向不同用户和任意采购品类，清单按每次采购独立设置，不设设备/食品/水果/服装品类白名单。测试品类仅为示例。指标名、数量、阈值和品目可以不同；受控的是可执行的规则类型和资源上限，复杂要求可保存为人工复核项。用户可以修改自己的要求，Agent 从需求/订单提出的候选须经用户确认才能生效。

## A. 清单契约、手动草稿与确认版本（已完成）

完成（2026-10-04）：详见 [交付记录](../../dynamic-checklists-a-2026-10-04.md)。A 交付时定向 36 项、完整离线 924 passed / 32 skipped（网络 0）、真实 MySQL 8 项通过；当时数据库 head 为 0014。以下 A 记录为阶段历史，最新实现与验证见 B。

- [x] 新建 `backend/app/criteria/models.py`：品目、稳定指标 ID、中文名称、有限规则和用户设定来源；数值用有限十进制字符串，拒绝浮点精度、NaN、任意执行代码。数值/区间、金额（币种、含税/计价单位）、枚举、资料存在、期限（起算事件/日历）和人工复核均有明确契约。条件不清或暂不支持的规则保留人工标记。上限二十品目、一百指标是首版容量保护，不宣称该上限已完成模型性能验收。
- [x] 新建 `db/checklist_schema_v1.py`、`checklist_repository.py`，迁移 `0014_dynamic_checklists.py`，扩展 models：可变 head/草稿、不可变确认版本、修改/确认操作记录。Case 锁 + expected_revision（资料）+ expected_checklist_revision（草稿）+ actor 范围幂等。只有明确 confirmed=true 才生成确认版本；版本不可原地修改。
- [x] 用户设定要求可以与原文不同，来源明确为 user_defined，保留作者/理由；可附买方需求原文作为背景，验证同 Case/快照/资料角色和原句，不能伪装成模型从原文抽取。文件型要求的严格抽取和语义验证在 B 完成。
- [x] 新建 `api/checklists.py` 路由，GET/PUT 草稿、POST 确认、GET 指定确认版本。沿用 AuthorizedRoute 项目权限，事务内再检查写入角色，资源删除/升版/被撤权拒绝写入。确认对选定指标集合生成版本，未选定项保留草稿并返回排除 ID；连续部分确认累积已选指标，重新 PUT 则替换完整草稿定义，旧版本保留历史。初始阶段不声称全量核验。
- [x] 清单存在时，旧八字段运行器明确返回 checklist_execution_pending，禁止默默按八项执行。保存及确认清单前均拒绝当前 Case 的 queued/running/waiting_user Run；已有报告保持只读旧版含义。B 完成后由新执行器替换此保护。
- [x] Case 删除清理移除 head、确认版本与操作记录；新资料下旧清单保留历史且显示需要重新确认，不跨快照自动生效。
- [x] 先写实际回归并确认失败，再实现：超过八项、食品品目、金额口径缺失、版本冲突、同 key 不同请求、不同 actor、原文串 Case/供应商、确认部分集合、升版、删除、运行中确认拒绝和旧运行器保护。完成 Ruff / 迁移 metadata / 完整离线与真实 MySQL 定向验收，独立审查。

API 契约：

- `GET /api/v1/cases/{case_id}/snapshots/{snapshot_id}/checklist` 返回资料 revision、草稿 revision、草稿、当前确认版本和执行是否已接入。
- `PUT .../checklist` 接受 expected_revision、expected_checklist_revision、items、criteria 和 reason；Header `Idempotency-Key`。
- `POST .../checklist/confirm` 接受上述两个 revision、confirmed=true、criterion_ids（至少一个）和 reason；Header `Idempotency-Key`。
- `GET /api/v1/cases/{case_id}/checklist-versions/{version_id}` 按项目权限读取不可变历史版本。

示例首版指标（阈值为用户设定，非原文事实）：

```json
{
  "criterion_id":"rice-price", "item_id":"rice", "name":"含税单价",
  "rule":{"type":"money","operator":"lte","value":"120","currency":"CNY","per_unit":"袋","tax_basis":"included"},
  "source":{"kind":"user_defined","reason":"本次采购预算要求"},
  "required":true, "enabled":true
}
```

## B. Agent 提出候选、动态事实与确定性核验

后端主流程交付（2026-10-04）：见 [实现、测试与边界](../../dynamic-checklists-b-2026-10-04.md)。最终离线 1123 passed / 35 skipped（网络 0），真实 MySQL 11 项通过，head 为 0015_dynamic_agent；合成原文 DeepSeek 共 8 次请求。以上为 B 主流程交付当时记录；自然语言修改、普通聊天与检索路由已于2026-10-05补齐，详见 [统一收尾](../../backend-completion-2026-10-05.md)。

- [x] 从服务端已确认买方角色的需求/订单逐批读取原文；使用现有 Provider 结构化输出提出候选，逐项验证原文、阈值、单位、比较关系和品目，歧义/冲突保留 needs_resolution。供应商资料不能自动生成买方标准。
- [x] 加入 propose_checklist / get_checklist 工具和候选保存协议；保存/确认由有权限的用户操作，模型不能代确认。
- [x] 聊天“增加/修改要求”产生绑定基准的草稿候选与程序变更摘要；用户明确保存确认后生效，不自动确认。
- [x] 泛化事实为 criterion_id 绑定，保留原文支持、供应商/品目/口径及完整覆盖记录；有限规则本地判定。自由文本/证照真实有效性/缺日历条件进入人工复核，不执行模型代码。
- [x] Run、恢复记忆、结果、修正及导出绑定清单版本；加入分批进度与预算，资料/清单变更使旧当前结果失效，旧报告可读。用新执行器解除 A 的明确保护，保留原八字段报告兼容。
- [x] 先做离线 Provider 与食品/设备测试，再告知必要真实模型发送范围及成本，执行小规模真实探针。真实探针仅合成苹果数量，不代替 C 的多品类真实模型质量验收。

## C. 接口兼容和多品类后端验收（已完成）

2026-10-05：完成上述后端实现与业务链路回归。五类指标 + 多品目回归采用脚本 Provider，真实模型仅小规模合成苹果验收；不宣称真实多品类准确率。数据、来源、冲突、版本、取消/恢复等由全量1178项中的各模块测试覆盖。前端样式待讨论、企业发布验收另行执行。见 [最终收尾](../../backend-completion-2026-10-05.md)。

- [x] 补齐 B 的自然语言要求变更候选与摘要，验证不会自动确认或修改供应商事实；普通聊天与语义检索路由已接入新的 assistant 消息操作。
- [x] 水果、服装、电子产品及包装食品示例逐项结果/必要来源对照，覆盖冲突、无证据、不同单位/计价、订单要求冲突、多品目同名、部分确认、超规模及取消恢复。这些示例不限定产品服务范围。
- [x] 验证完整审查按已确认集合枚举，未确认/停用/人工项不被漏算为成功；确认版本、事实、结果和审计可解释。
- [x] 既有八字段 API/历史/修正/导出完整回归；新 schema 与未来聊天前端交接。前端具体规范与样式按用户约定另外讨论，不在本计划直接写页面。

## 参考与取舍

[Pydantic discriminated unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions)推荐显式判别字段；本项目按 rule.type 选择有限 schema，避免模型或用户输入不确定地落到错误类型。[LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output)说明结构化响应及验证错误处理；B 复用现有 Provider/工具协议，不增加一套独立聊天 SDK。
