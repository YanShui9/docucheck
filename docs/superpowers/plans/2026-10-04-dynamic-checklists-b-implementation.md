# 动态清单 B 实施契约

> 实施更新（2026-10-05）：动态清单、普通聊天、自然语言变更候选、上下文/分层记忆及真实混合检索已完成后端接入与统一验收，详见 [收尾报告](../../backend-completion-2026-10-05.md)。下方“待实现/未重启”等按原阶段理解为历史；模板库、自然语言自动保存偏好、私有会话和前端视觉改版仍未实现。前端先讨论规范及样式。

沿用已批准的 A/B/C 计划，本轮实现 B 的后端主流程，不改前端、不操作其他 AI 的检索部署。产品面向任意用户和品类，复杂要求明确人工复核，不把模型输出作为正式事实。实际验收和剩余项见 [B 交付记录](../../dynamic-checklists-b-2026-10-04.md)：自然语言要求变更候选尚未实现，仍在总计划中保留未完成标记，不能将本内部契约当作 B 全部完成。

实现分工与接入约定：

1. `criteria/models.py` 扩展 `DocumentSource(kind=document_extracted, context=EvidenceContext)`；任何文档提取要求均校验来源、阈值/比较符/单位口径，不支持的候选留 needs_resolution；用户可明确改为 user_defined。候选不代用户确认。
2. `criteria/facts.py`：FactCandidate 包含 criterion_id/item_id/supplier/value/unit/evidence_refs/quote，可选 calendar/start_event/currency/per_unit/tax_basis；不接受模型指定事实 ID。`validate_fact(candidate, definition, repo, supplier)` 返回来源验证的 JSON 事实（含 fact_id）；`evaluate_checklist(definition, facts, coverage, *, snapshot_id)` 返回兼容 CheckOut 并包含 criterion_id/item_id/criterion_name 的逐项结果。coverage 按 supplier 的 bool 完整处理标记；完整审查目标由程序枚举，不由 RAG top-k 或模型任意删减。
3. `criteria/validation.py`：`rule_supported(criterion, item)` 验证文档型要求的原句、指标/品目、阈值及有限比较口径；`validate_definition(definition, repo)` 验证全部来源范围。手工要求允许与原文不同，不修改供应商事实。
4. `agent/dynamic.py`：`DynamicAgent(repo, provider, definition=None, *, mode='standard', validate=lambda:None, cache_load=None, cache_save=None, history=None, memory_layers=None, history_reader=None)`；`ask(question, *, operation='review') -> dict`，返回 status/answer/error/usage/payload/events。payload 包含 definition（proposal）、facts/checks/coverage、rule_version、source_digest、version=1；全部结果来自服务端枚举及来源验证。缓存函数 `(batch_id, input_digest) -> output|None` 和 `(batch_id,input_digest,output) -> None`。get_checklist/propose_checklist/review_checklist/read_sources 工具只接受本 scope 的参数。模型不确认清单，不执行任意代码。LangGraph 受控循环/抽取/核验；有限预算、输出协议修复最多一次，取消/来源检查在每次调用/缓存/最终回答前。
5. 数据库新增 `dynamic_runs`（原 Run 的工作流与清单版本绑定）、`dynamic_batches`（输入/输出指纹与有效批次）、`dynamic_corrections`（原文支持的事实修正审计）；不回写历史八字段结果。提交消息可指定 operation=propose_checklist；确认清单存在时普通消息进入动态路径，未确认草稿拒绝审查。重试必须继续原版本，资料或清单变化阻断旧 Run。
6. 运行复用原 attempt/fence、权限、心跳、取消与 outbox；结构化动态结果由独立 payload 保存。近期/偏好装配复用已有机制，历史任务 checkpoint 标记动态版本且绑定确认清单，动态流程不恢复旧八字段事实。导出/结果读取重验原文、事实及规则；人工修正只纠正原文支持事实，同源双值保留冲突。
7. 离线脚本 Provider 测试水果/服装/电子示例和异常/版本/权限/恢复，真实 MySQL 定向测试及全量离线；真实模型探针先 preview 明示数据与调用上限，外部实测不混入离线成绩。

上述内部模块分工不增加对用户的阶段或确认点。旧页面和服务进程不自动重启；候选确认入口先通过 API，后续聊天前端仍先讨论设计。
