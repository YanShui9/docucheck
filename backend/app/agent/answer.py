"""Render only verified structured results and original source quotations."""
import re

from .tools import AnswerDraft, ToolExecutor

REASONS = {
    "coverage_incomplete": "资料解析或全量抽取尚未完成，不能作正式判定。",
    "requirement_missing": "完整处理后没有找到可用的内存需求。",
    "supplier_fact_missing": "完整处理后没有找到适用的供应商内存证据。",
    "semantics_unclear": "内存口径、型号关系或需求语义需要确认。",
    "supplier_capacity_not_exact": "供应商未提供可直接比较的确定内存容量。",
    "contradicting_installed_capacity": "同一供应商、型号的已装内存来源数值不一致。",
    "threshold_met": "内存容量符合明确的需求阈值。",
    "threshold_failed": "内存容量不符合明确的需求阈值。",
}


def render_answer(draft: AnswerDraft, tools: ToolExecutor, question: str) -> tuple[str, str]:
    if len(draft.check_result_ids) > 1:
        raise ValueError("answer_invalid")
    if draft.kind == "check" and not draft.check_result_ids:
        raise ValueError("answer_invalid")
    conflict_sources = (re.search(r"冲突|矛盾", question) is not None
                        and re.search(r"哪|来源|证据|根据|依据|为什么", question) is not None)
    conflict_sources |= (re.search(r"哪\s*(?:两|二|2)\s*处", question) is not None
                         and re.search(r"判断|根据|依据|来源|证据", question) is not None)
    if not draft.check_result_ids and (re.search(r"达标|符合|满足|合格", question)
                                       or conflict_sources):
        raise ValueError("answer_invalid")
    lines: list[str] = []
    refs: list[str] = []
    result_refs: set[str] = set()
    status = "completed"
    for key in draft.check_result_ids:
        result = tools.results.get(key)
        if (result is None or result.snapshot_id != tools.repo.snapshot_id
                or result.field_key != "memory"):
            raise ValueError("answer_invalid")
        result_refs.update(result.evidence_refs)
        refs.extend(result.reason_evidence_refs if conflict_sources
                    and result.reason_code == "contradicting_installed_capacity"
                    else result.evidence_refs)
        lines.append(f"{result.supplier} · 内存：{result.business_status or '处理未完成'}。"
                     f"{REASONS[result.reason_code]}")
        if result.processing_status != "complete":
            status = "partial"
    if draft.check_result_ids and not set(draft.evidence_ids) <= result_refs:
        raise ValueError("answer_invalid")
    if not conflict_sources or not draft.check_result_ids:
        refs.extend(draft.evidence_ids)
    refs = list(dict.fromkeys(refs))
    if (not refs and not draft.check_result_ids) or not set(refs) <= tools.seen:
        raise ValueError("answer_invalid")
    for block in tools.repo.read(refs):
        location = f"第 {block.page_idx + 1} 页" if block.page_idx is not None else f"{block.sheet}!{block.cell}"
        # Quotes are explicitly delimited, never interpreted as executable content.
        text = block.text[:1000]
        suffix = "（原文片段已截断）" if len(block.text) > 1000 else ""
        lines.append(f"来源：{block.document_id} · {location}\n原文：{text}{suffix}")
    return "\n\n".join(lines), status
