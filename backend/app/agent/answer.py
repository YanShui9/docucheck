"""Render only verified structured results and original source quotations."""
import re
from dataclasses import dataclass

from ..field_contract import FieldCheckResult, FieldKey
from ..rules.memory import CheckResult
from .tools import AnswerDraft, ToolExecutor

REASONS = {
    "coverage_incomplete": "资料解析或全量抽取尚未完成，不能作正式判定。",
    "requirement_missing": "完整处理后没有找到可用的内存需求。",
    "supplier_fact_missing": "完整处理后没有找到适用的供应商内存证据。",
    "supplier_capacity_not_exact": "供应商未提供可直接比较的确定内存容量。",
    "contradicting_installed_capacity": "同一供应商、型号的已装内存来源数值不一致。",
    "threshold_met": "内存容量符合明确的需求阈值。",
    "threshold_failed": "内存容量不符合明确的需求阈值。",
    "extraction_failed": "字段抽取失败，不能作正式判定。",
    "requirement_unclear": "采购需求的口径需要确认。",
    "semantics_unclear": "单位、限定语、型号或计价口径需要确认。",
    "conflicting_supplier_values": "同一供应商、型号与口径的资料数值不一致。",
}

FIELD_NAMES = {
    "memory": "内存", "storage": "存储", "screen_size": "屏幕尺寸",
    "weight": "重量", "warranty": "质保", "quantity": "采购数量",
    "unit_price": "单价", "delivery_days": "交付期限",
}
FIELD_QUESTIONS = {
    "memory": r"内存|RAM", "storage": r"存储|SSD|硬盘",
    "screen_size": r"屏幕|显示屏", "weight": r"重量",
    "warranty": r"质保|保修", "quantity": r"数量",
    "unit_price": r"单价", "delivery_days": r"交付|交货|交期",
}


@dataclass(frozen=True)
class QuestionScope:
    suppliers: frozenset[str] | None
    fields: frozenset[str] | None
    required_pairs: frozenset[tuple[str, str]] | None
    full_review: bool


def resolve_question_scope(question: str, tools: ToolExecutor,
                           required_supplier: str | None = None) -> QuestionScope:
    compact = re.sub(r"\s+", "", question)
    all_suppliers = {source["supplier"] for source in tools.repo.sources()
                     if source["supplier"]}
    all_supplier_request = re.search(r"所有供应商|全部供应商|各家|每家", compact)
    all_field_request = re.search(
        r"八项指标|八项结果|全部八项|所有八项|全部字段|所有字段|各项指标|全部指标|所有指标",
        compact,
    )
    review_intent = re.search(r"审查|核对|核验|评估|比较|对比|达标|满足|结果|结论", compact)
    source_intent = re.search(r"原文|来源|证据|资料|文件", compact)
    full = bool(re.search(r"完整审查|全面审查", compact)
                or (all_field_request and review_intent)
                or (all_supplier_request and review_intent and not source_intent))
    requested_fields = {field for field, pattern in FIELD_QUESTIONS.items()
                        if re.search(pattern, compact, re.IGNORECASE)}
    if all_field_request or (full and not requested_fields):
        requested_fields = {field.value for field in FieldKey}
    requested_suppliers = set()
    aliases = {alias.casefold() for group in re.findall(
        r"供应商([A-Za-z](?:(?:、|,|，|和|及|与)[A-Za-z])*)(?![A-Za-z0-9])",
        compact, re.IGNORECASE,
    ) for alias in re.split(r"[、,，和及与]", group)}
    for group in re.findall(
        r"(?<![A-Za-z])([A-Za-z](?:(?:、|,|，|和|及|与)[A-Za-z])+)(?![A-Za-z])",
        compact, re.IGNORECASE,
    ):
        aliases.update(alias.casefold() for alias in re.split(r"[、,，和及与]", group))
    aliases.update(alias.casefold() for alias in re.findall(
        r"(?<![A-Za-z])([A-Za-z])的(?:报价|资料|来源|原文|证据|内存|存储|屏幕|重量|质保|数量|单价|交付)",
        compact, re.IGNORECASE,
    ))
    for supplier in all_suppliers:
        suffix = supplier.rsplit("-", 1)[-1]
        if (re.search(rf"(?<![A-Za-z0-9_-]){re.escape(supplier)}(?![A-Za-z0-9_-])",
                      compact, re.IGNORECASE)
                or (len(suffix) == 1 and suffix.isalpha()
                    and suffix.casefold() in aliases)):
            requested_suppliers.add(supplier)
    if all_supplier_request:
        requested_suppliers = all_suppliers
    elif required_supplier and not requested_suppliers:
        requested_suppliers.add(required_supplier)
    elif full and not requested_suppliers and len(all_suppliers) == 1:
        requested_suppliers = all_suppliers
    suppliers = frozenset(requested_suppliers) if requested_suppliers else None
    fields = frozenset(requested_fields) if requested_fields else None
    pairs = (frozenset((supplier, field) for supplier in suppliers for field in fields)
             if suppliers and fields else None)
    return QuestionScope(suppliers, fields, pairs, full)


def _answer_scope(question: str, tools: ToolExecutor, results: list,
                  required_supplier: str | None = None) -> QuestionScope:
    scope = resolve_question_scope(question, tools, required_supplier)
    if scope.suppliers and any(result.supplier not in scope.suppliers for result in results):
        raise ValueError("answer_invalid")
    if scope.fields and any(str(result.field_key) not in scope.fields for result in results):
        raise ValueError("answer_invalid")
    if scope.full_review and scope.required_pairs is None:
        raise ValueError("answer_invalid")
    if scope.full_review and not results:
        raise ValueError("answer_invalid")
    if results and scope.required_pairs is not None and {
        (result.supplier, str(result.field_key)) for result in results
    } != scope.required_pairs:
        raise ValueError("answer_invalid")
    if not scope.full_review and not scope.suppliers and len({result.supplier for result in results}) > 1:
        raise ValueError("answer_invalid")
    if not scope.full_review and not scope.fields and len({result.field_key for result in results}) > 1:
        raise ValueError("answer_invalid")
    return scope


def render_answer(draft: AnswerDraft, tools: ToolExecutor, question: str,
                  *, required_supplier: str | None = None) -> tuple[str, str]:
    if draft.kind == "check" and not draft.check_result_ids:
        raise ValueError("answer_invalid")
    conflict_sources = (re.search(r"冲突|矛盾", question) is not None
                        and re.search(r"哪|来源|证据|根据|依据|为什么", question) is not None)
    conflict_sources |= (re.search(r"哪\s*(?:两|二|2)\s*处", question) is not None
                         and re.search(r"判断|根据|依据|来源|证据", question) is not None)
    if not draft.check_result_ids and (re.search(r"达标|符合|满足|合格|完整审查|全面审查", question)
                                       or conflict_sources):
        raise ValueError("answer_invalid")
    if len(set(draft.check_result_ids)) != len(draft.check_result_ids):
        raise ValueError("answer_invalid")
    results = [tools.results.get(key) for key in draft.check_result_ids]
    if any(result is None or result.snapshot_id != tools.repo.snapshot_id
           or (isinstance(result, CheckResult) and result.field_key != "memory")
           or not isinstance(result, (CheckResult, FieldCheckResult)) for result in results):
        raise ValueError("answer_invalid")
    if len({(result.supplier, result.field_key) for result in results}) != len(results):
        raise ValueError("answer_invalid")
    scope = _answer_scope(question, tools, results, required_supplier)
    lines: list[str] = []
    refs: list[str] = []
    result_refs: set[str] = set()
    status = "completed"
    for result in results:
        result_refs.update(result.evidence_refs)
        refs.extend(result.reason_evidence_refs if conflict_sources
                    and result.reason_code in {"contradicting_installed_capacity",
                                               "conflicting_supplier_values"}
                    else result.evidence_refs)
        field = str(result.field_key)
        reason = REASONS.get(result.reason_code, "请复核此字段的证据和口径。")
        if isinstance(result, FieldCheckResult):
            label = FIELD_NAMES[field]
            reason = {
                "requirement_missing": f"完整处理后没有找到可用的{label}需求。",
                "supplier_fact_missing": f"完整处理后没有找到适用的供应商{label}证据。",
                "threshold_met": f"{label}符合明确的需求阈值。",
                "threshold_failed": f"{label}不符合明确的需求阈值。",
            }.get(result.reason_code, reason)
        lines.append(f"{result.supplier} · {FIELD_NAMES[field]}："
                     f"{result.business_status or '处理未完成'}。{reason}")
        if result.processing_status != "complete":
            status = "partial"
    if draft.check_result_ids and not set(draft.evidence_ids) <= result_refs:
        raise ValueError("answer_invalid")
    if not conflict_sources or not draft.check_result_ids:
        refs.extend(draft.evidence_ids)
    refs = list(dict.fromkeys(refs))
    if (not refs and not draft.check_result_ids) or not set(refs) <= tools.seen:
        raise ValueError("answer_invalid")
    source_blocks = tools.repo.read(refs)
    if (not results and scope.suppliers and len(scope.suppliers) > 1
            and {block.supplier for block in source_blocks if block.supplier} != scope.suppliers):
        raise ValueError("answer_invalid")
    for block in source_blocks:
        if scope.suppliers and block.supplier not in (None, *scope.suppliers):
            raise ValueError("answer_invalid")
        location = f"第 {block.page_idx + 1} 页" if block.page_idx is not None else f"{block.sheet}!{block.cell}"
        # Quotes are explicitly delimited, never interpreted as executable content.
        text = block.text[:1000]
        suffix = "（原文片段已截断）" if len(block.text) > 1000 else ""
        lines.append(f"来源：{block.document_id} · {location}\n原文：{text}{suffix}")
    return "\n\n".join(lines), status
