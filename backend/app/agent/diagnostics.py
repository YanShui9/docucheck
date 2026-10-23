"""Bounded diagnostics without prompts, tool arguments, source text or credentials."""

import re

from ..field_contract import FieldKey
from ..providers.base import ProviderError
from .context_usage import CONTEXT_PHASES, ESTIMATE_METHOD, safe_memory_counts
from .tools import DEFINITIONS

SAFE_VALIDATION_DETAILS = frozenset({
    "fact_field", "fact_value", "fact_qualifier", "fact_comparator",
    "candidate_scope", "candidate_pair", "candidate_field", "candidate_value",
    "extraction_schema", "extraction_block_too_large",
})
SAFE_COVERAGE_TYPES = frozenset({
    "batch_too_large", "batch_schema", "unexpected_field",
    "candidate_invalid_scope", "candidate_invalid_uncovered",
    "unverified_source", "unverified_value", "memory_coverage_incomplete",
})
DYNAMIC_REJECTION_CODES = frozenset({
    "candidate_scope", "candidate_criterion", "unknown_criterion", "unknown_item",
    "supplier_scope", "evidence_scope", "evidence_relationship", "evidence_quote",
    "fact_criterion", "fact_item", "fact_value", "fact_unit", "fact_currency",
    "fact_per_unit", "fact_tax_basis", "fact_calendar", "fact_start_event",
    "candidate_invalid", "item_binding_scope", "item_binding_source_changed",
    "material_sources_rule", "material_sources_overlap", "material_source_criterion", "material_source_item",
})
_TOOLS = frozenset(DEFINITIONS) | {
    "get_checklist", "propose_checklist", "review_checklist", "read_sources", "batch_cache",
    "read_checklist", "answer_checklist", "read_task_history", "read_conversation_history", "read_first_task_message",
}
_ERRORS = ProviderError._CODES | frozenset({
    "invalid_tool_result", "evidence_scope", "supplier_scope", "fact_set_scope",
    "unknown_tool", "answer_invalid", "decision_budget", "input_budget",
    "output_budget", "model_budget", "tool_budget", "deadline", "cancelled",
    "memory_context_changed", "history_scope", "candidate_invalid", "cache_invalid",
    "checklist_read_no_progress", "checklist_read_scope_budget", "checklist_edit_scope_budget",
    "context_budget", "token_budget",
})
_DETAILS = SAFE_VALIDATION_DETAILS | ProviderError._DETAILS | {
    "schema_validation", "other_validation",
    "history_scope_rejected", "history_no_progress",
}
_STATUSES = {"started", "completed", "failed", "rejected"}
_FIELDS = {field.value for field in FieldKey}
MAX_DIAGNOSTIC_EVENTS = 128


def _integer(value):
    return type(value) is int and 0 <= value <= 2_147_483_647


def _safe_context(value) -> dict:
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        return {}
    result = {"version": 1}
    for key in ("model_window_tokens", "legacy_input_estimate", "legacy_schema_bytes",
                "legacy_task_input_remaining", "output_requested_tokens", "output_planned_tokens",
                "safety_reserved_tokens", "task_input_hard_limit_tokens", "task_output_hard_limit_tokens"):
        if _integer(value.get(key)):
            result[key] = value[key]
    for key in ("legacy_task_input_remaining", "task_input_hard_limit_tokens", "task_output_hard_limit_tokens"):
        if key in value and value[key] is None:
            result[key] = None
    for key, expected in (("input_estimate_unit", "utf8_bytes"),
                          ("window_fit_method", "one_byte_per_token_allowance_v1")):
        if value.get(key) == expected:
            result[key] = expected
    # The legacy limit can be negative when output alone exceeds the window.
    limit = value.get("legacy_prompt_limit")
    if type(limit) is int and -2_147_483_647 <= limit <= 2_147_483_647:
        result["legacy_prompt_limit"] = limit
    if isinstance(value.get("phase"), str) and value["phase"] in CONTEXT_PHASES:
        result["phase"] = value["phase"]
    if value.get("estimate_method") == ESTIMATE_METHOD:
        result["estimate_method"] = ESTIMATE_METHOD
    if value.get("exact_tokens") is False:
        result["exact_tokens"] = False
    if isinstance(value.get("request_digest"), str) and re.fullmatch(r"[a-f0-9]{64}", value["request_digest"]):
        result["request_digest"] = value["request_digest"]
    return result


def safe_trace_events(events) -> list[dict]:
    """Project raw local graph events onto a finite public diagnostic vocabulary."""
    if not isinstance(events, (tuple, list)):
        return []
    result = []
    if len(events) > MAX_DIAGNOSTIC_EVENTS:
        result.append({"kind": "agent_trace_truncated", "summary": {
            "omitted_events": min(len(events) - MAX_DIAGNOSTIC_EVENTS, 2_147_483_647),
        }})
    for event in events[-MAX_DIAGNOSTIC_EVENTS:]:
        if (not isinstance(event, dict) or not isinstance(event.get("kind"), str)
                or event["kind"] not in {"model", "tool", "policy"}):
            continue
        kind = event["kind"]
        summary = {"status": event.get("status") if isinstance(event.get("status"), str)
                   and event["status"] in _STATUSES else "other"}
        if _integer(event.get("sequence")):
            summary["sequence"] = event["sequence"]
        if kind == "model":
            memory = safe_memory_counts(event.get("memory"))
            if memory:
                summary["memory"] = memory
            context = _safe_context(event.get("context"))
            if context:
                summary["context"] = context
            if type(event.get("provider_called")) is bool:
                summary["provider_called"] = event["provider_called"]
            summary["operation"] = (event.get("operation") if isinstance(event.get("operation"), str)
                                    and event["operation"] in {"decide", "extract"} else "other")
            usage = event.get("usage")
            if isinstance(usage, dict):
                summary["usage"] = {key: usage[key] for key in (
                    "input_tokens", "output_tokens", "total_tokens",
                ) if _integer(usage.get(key))}
            for key in ("tool_call_count", "serialized_tool_calls", "history_turns_dropped"):
                if _integer(event.get(key)):
                    summary[key] = event[key]
            if isinstance(event.get("policy_override"), str) and event["policy_override"] in DEFINITIONS:
                summary["policy_override"] = event["policy_override"]
            for key in ("structured_repair", "semantic_repair"):
                if type(event.get(key)) is bool:
                    summary[key] = event[key]
        elif kind == "tool":
            summary["tool"] = event.get("tool") if isinstance(event.get("tool"), str) and event["tool"] in _TOOLS else "other"
            for key in ("ordinal", "rejected_count", "request_parts", "completed_parts", "context_repack_count"):
                if _integer(event.get(key)):
                    summary[key] = event[key]
            for key in ("initial_rejection_codes", "rejection_codes"):
                if isinstance(event.get(key), dict):
                    summary[key] = {code: event[key][code] for code in sorted(DYNAMIC_REJECTION_CODES)
                                    if code in event[key] and _integer(event[key][code])}
        else:
            summary["action"] = "submit_answer" if event.get("action") == "submit_answer" else "other"
            summary["reason"] = ("full_review_evaluated" if event.get("reason") == "full_review_evaluated"
                                 else "other")
        for key, vocabulary in (("error", _ERRORS), ("failure_detail", _DETAILS)):
            if key in event:
                summary[key] = event[key] if isinstance(event[key], str) and event[key] in vocabulary else "other"
        if type(event.get("outcome_unknown")) is bool:
            summary["outcome_unknown"] = event["outcome_unknown"]
        coverage = event.get("field_coverage")
        if isinstance(coverage, list):
            rows = []
            for item in coverage[:8]:
                if (not isinstance(item, dict) or not isinstance(item.get("field_key"), str)
                        or item["field_key"] not in _FIELDS or type(item.get("complete")) is not bool):
                    continue
                types = item.get("issue_types")
                rows.append({"field_key": item["field_key"], "complete": item["complete"],
                             "issue_types": sorted({value if value in SAFE_COVERAGE_TYPES else "other"
                                                    for value in types[:16] if isinstance(value, str)})
                             if isinstance(types, list) else []})
            summary["field_coverage"] = rows
        result.append({"kind": f"agent_{kind}", "summary": summary})
    return result
