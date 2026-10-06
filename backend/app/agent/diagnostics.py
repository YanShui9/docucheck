"""Bounded diagnostics without prompts, tool arguments, source text or credentials."""

from ..field_contract import FieldKey
from ..providers.base import ProviderError
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
_ERRORS = ProviderError._CODES | frozenset({
    "invalid_tool_result", "evidence_scope", "supplier_scope", "fact_set_scope",
    "unknown_tool", "answer_invalid", "decision_budget", "input_budget",
    "output_budget", "model_budget", "tool_budget", "deadline", "cancelled",
    "memory_context_changed", "history_scope",
})
_DETAILS = SAFE_VALIDATION_DETAILS | ProviderError._DETAILS | {
    "schema_validation", "other_validation",
}
_STATUSES = {"started", "completed", "failed", "rejected"}
_FIELDS = {field.value for field in FieldKey}
MAX_DIAGNOSTIC_EVENTS = 128


def _integer(value):
    return type(value) is int and 0 <= value <= 2_147_483_647


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
            if type(event.get("structured_repair")) is bool:
                summary["structured_repair"] = event["structured_repair"]
        elif kind == "tool":
            summary["tool"] = event.get("tool") if isinstance(event.get("tool"), str) and event["tool"] in DEFINITIONS else "other"
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
