"""Diagnostic persistence cannot expose arbitrary model or transport data."""

import copy
import json

from backend.app.agent.diagnostics import MAX_DIAGNOSTIC_EVENTS, safe_trace_events


def test_diagnostics_drop_unknown_shapes_and_unhashable_values():
    assert safe_trace_events({"kind": "tool"}) == []
    assert safe_trace_events([None, "SECRET", {"kind": ["tool"]}]) == []
    assert safe_trace_events([{"kind": "model", "status": {}, "operation": []}]) == [
        {"kind": "agent_model", "summary": {"status": "other", "operation": "other"}},
    ]


def test_diagnostics_bound_history_and_retain_last_failure():
    events = [{"kind": "model", "operation": "decide", "status": "completed", "sequence": i}
              for i in range(1, MAX_DIAGNOSTIC_EVENTS + 10)]
    events[-1].update(status="failed", error="invalid_output", failure_detail="output_limit")
    trace = safe_trace_events(events)
    assert len(trace) == MAX_DIAGNOSTIC_EVENTS + 1
    assert trace[0] == {"kind": "agent_trace_truncated", "summary": {"omitted_events": 9}}
    assert trace[-1]["summary"]["failure_detail"] == "output_limit"


def test_diagnostics_coverage_and_error_labels_are_finite_and_do_not_mutate_input():
    raw = {"kind": "tool", "tool": "SECRET", "status": "SECRET", "sequence": True,
           "error": "SECRET", "failure_detail": "SECRET", "outcome_unknown": "SECRET",
           "evidence_refs": ["SECRET"], "arguments": "SECRET",
           "field_coverage": [{"field_key": "storage", "complete": False,
                               "issue_types": ["unverified_value", "SECRET", {}],
                               "source_text": "SECRET"},
                              {"field_key": "SECRET", "complete": True},
                              {"field_key": "memory", "complete": "SECRET"}]}
    before = copy.deepcopy(raw)
    trace = safe_trace_events([raw])
    assert trace == [{"kind": "agent_tool", "summary": {
        "tool": "other", "status": "other", "error": "other", "failure_detail": "other",
        "field_coverage": [{"field_key": "storage", "complete": False,
                            "issue_types": ["other", "unverified_value"]}],
    }}]
    assert "SECRET" not in json.dumps(trace)
    assert raw == before


def test_policy_diagnostic_is_distinct_from_model_usage_and_drops_arbitrary_content():
    raw = {"kind": "policy", "action": "submit_answer", "reason": "full_review_evaluated",
           "status": "completed", "sequence": 5, "arguments": {"secret": "SECRET"},
           "prompt": "SECRET", "usage": {"input_tokens": 999}, "token": "SECRET"}
    assert safe_trace_events([raw]) == [{"kind": "agent_policy", "summary": {
        "status": "completed", "sequence": 5, "action": "submit_answer",
        "reason": "full_review_evaluated",
    }}]


def test_structured_repair_diagnostic_preserves_only_boolean_attempt_marker():
    events = [{"kind": "model", "status": "completed", "operation": "extract",
               "structured_repair": True, "arguments": "SECRET"},
              {"kind": "model", "status": "completed", "operation": "extract",
               "structured_repair": "SECRET"}]
    trace = safe_trace_events(events)
    assert trace[0]["summary"]["structured_repair"] is True
    assert "structured_repair" not in trace[1]["summary"]
    assert "SECRET" not in json.dumps(trace)


def test_history_trim_diagnostic_contains_a_bounded_count_only():
    trace = safe_trace_events([{"kind": "model", "operation": "decide", "status": "completed",
                               "history_turns_dropped": 2, "history": "SECRET"},
                              {"kind": "model", "operation": "decide", "status": "completed",
                               "history_turns_dropped": "SECRET"}])
    assert trace[0]["summary"]["history_turns_dropped"] == 2
    assert "history_turns_dropped" not in trace[1]["summary"]
    assert "SECRET" not in str(trace)
