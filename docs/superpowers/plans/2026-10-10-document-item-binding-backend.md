# 资料与品目对应：后端步骤 A 实施计划

> For agentic workers: 使用 subagent-driven-development，按任务实施并做规格、代码两次复核。用户已确认完整设计；本计划只执行 A，B 前端和 C 真实模型闭环在 A 完成后执行。

**目标：** 可确认、可追溯的品目对应参与新审查与范围检索，旧报告维持原语义。

**架构：** 原件、EvidenceRepository 和旧规则不改身份。MySQL 存不可变对应版本与 head；新 Run 冻结版本及摘要，v4 才使用对应关系。范围是服务端展开的证据集合，锚点单独保存，不拼入事实 quote。

**技术栈：** 既有 Python 3.12、FastAPI、SQLAlchemy/Alembic、MySQL、LangGraph、Milvus；不安装/升级依赖。沿用当前非 main 的开发分支，服务和真实任务保持。

## A1：对应契约、持久化与授权 API

主要文件：新增 `backend/app/criteria/item_bindings.py`、`backend/app/db/item_binding_schema_v1.py`、`backend/app/db/item_binding_repository.py`、`backend/app/api/item_bindings.py`、`backend/migrations/versions/0017_item_bindings.py`；接入 `backend/app/db/models.py`、`backend/app/db/case_cleanup.py`、`backend/app/api/main.py`。测试新增 `backend/tests/test_item_bindings.py`、`backend/tests/test_item_binding_api.py`、`backend/tests/test_item_bindings_mysql.py`，更新迁移测试。

- [ ] 先写并运行失败测试：whole/XLSX 矩形/PDF 页面；同原件锚点；错误品目、旧 hash、跨文档、越界、不同品目重叠；旧泛品目名与 SKU 分类。
- [ ] 实现严格契约及 canonical 展开。每个 entry 保存 document_id/hash/parser、item_id、选择器、展开证据 refs、锚点和型号分类。未知类别不能消除型号守卫；具体 SKU 须有原文支持。
- [ ] 新建 versions/head/幂等操作表；Run adjunct 新列 `item_binding_version_id`、`item_binding_digest` 为 nullable，NULL/NULL 表示历史无此机制，不能默认为最新绑定。冻结旧迁移 schema 不改。
- [ ] PUT 确认整个对应清单，必须 explicit confirmed、前置资料/清单/对应 revision、actor、reason、Idempotency-Key。Case-first/current read，拒绝 queued/running 和失权；GET 当前与历史均受当前成员/删除检查。变化后旧 head 只读，不自动重用。
- [ ] Repository 提供事务内 `freeze_current`/冻结版本加载能力，供 A2 调用。无确认对应时冻结可验证的空集合及摘要；身份包含 Case、snapshot、清单版本。载入重验 hash/parser/范围/锚点及 canonical digest，不读取最新 head 重解释历史。
- [ ] 增加 Case 清理依赖顺序、SQLite 升降迁移/MySQL 方言与隔离 MySQL 并发测试；测试通过、复核后提交本地 Git。

## A2：v4 规则、Run、缓存、历史和修正

主要文件：`backend/app/criteria/facts.py`、`backend/app/agent/dynamic.py`、`backend/app/db/dynamic_repository.py`、`backend/app/db/dynamic_executor.py`、`backend/app/db/assistant_repository.py`、`backend/app/db/assistant_executor.py`，以及实际报告导出、进度与 task memory 接口。测试扩展 `backend/tests/test_dynamic_run_binding.py`、`backend/tests/test_dynamic_facts.py`、`backend/tests/test_dynamic_batch_retry.py`、`backend/tests/test_dynamic_report_types.py`，新增品目绑定执行回归。

- [ ] 首先 ScriptProvider/真实规则失败回归：苹果手机泛品目、数量独立行 10 台、订单确认后 18 个自然日，对 10 台/14 日要求；预期数量满足、交期不满足，manual 指标保持原状。
- [ ] 新提交动态/助手 Run 冻结 A1 返回的对应身份，内部 formal 注册沿用助手提交时的身份。retry/resume 不换版本；waiting_user 之后的绑定变更须新 Run。
- [ ] v4 才以明确对应支持事实 item applicability；数值、单位、同一行连续引用、supplier/hash/parser、原文反证和明确 SKU 守卫仍有效。范围未确认或解析 partial 不能当完整缺失；不改旧 v1/v2/v3 dump 和 digest。
- [ ] 批次 ID/input_digest、payload/report、结果重验、人工修正/备选事实、记忆与进度包括绑定身份。跨品目/版本缓存不复用；旧报告用冻结身份重放，原件变化拒绝。
- [ ] 模型每批仅看到必要范围/品目锚点说明，服务端持有全表。无绑定仍使用明确 v4 空身份并保守判定，普通聊天不重复注入对应表。
- [ ] 回归旧规则、助手上下文、修正、历史、缓存、权限与任务记忆；规格和代码复核后 Git 提交。

## A3：范围 RAG、统一复核与实连门禁

主要文件：`backend/app/vector_store/store.py`、`backend/app/evidence/hybrid.py`、`backend/app/evidence/retrieval.py`、`backend/app/db/persisted_retrieval.py`；测试 `backend/tests/test_hybrid_retrieval.py`、`backend/tests/test_persisted_retrieval.py`、`backend/tests/test_persisted_hybrid_agent.py` 及新范围用例。

- [ ] 先写失败测试：同文件苹果/服装，scope 只能返回本品目数值范围；BM25、dense、read_source 不能跨范围，锚点只有显式详情可读。
- [ ] 利用现有 chunk_id 做有限分批 Milvus scalar 过滤。允许集合由服务端绑定展开；拒绝不在集合的返回或跨范围底层证据。普通授权浏览与品目范围检索分开；不修改 embedding 文本/现有原件 chunk fingerprint。
- [ ] 粗 chunk 无法表达、过滤预算过大、索引未就绪或向量失败时返回范围内词法降级，给出明确 diagnostics。没有命中不作为正式审查完整覆盖证明。
- [ ] 审查全部适用原文，不以 top-k 替代覆盖。核对范围变化不会主动重建索引、旧 schema 兼容及 hash 不变。
- [ ] 全后端离线 pytest（网络请求为 0）、Ruff、依赖检查、迁移验证；通知后使用现有 Docker 的独立临时 MySQL/Milvus 资料做门禁，不改原采购任务、既有 waiting_user/索引。
- [ ] 总体复核并修复；更新 CLAUDE/路线图和实测报告，Git 提交。仅在证据支持时标记 A 完成，未做的前端/真实模型验收保持未勾选。

## 通用测试命令

```powershell
& E:/developTools/miniconda3/envs/docucheck/python.exe -m pytest backend/tests/test_item_bindings.py backend/tests/test_item_binding_api.py -q
& E:/developTools/miniconda3/envs/docucheck/python.exe -m pytest backend/tests -q
& E:/developTools/miniconda3/envs/docucheck/python.exe -m ruff check backend
& E:/developTools/miniconda3/envs/docucheck/python.exe -m pip check
git diff --check
```

需要真实依赖的测试使用项目既有显式开关和临时数据库/集合清理策略。失败、跳过和未执行门禁分开报告，不使用旧测试数字声明当前版本通过。
