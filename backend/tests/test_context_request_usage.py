"""Request diagnostics retain units and never expose the measured contents."""

import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.app.agent.context_usage import measure_request, reserve_with_context
from backend.app.agent.diagnostics import safe_trace_events
from backend.app.agent.policy import Budget, RunStopped, input_bound


def test_measure_complete_request_without_claiming_exact_tokens_or_mutating_inputs():
    messages = [SystemMessage(content="仅依原文回答"), HumanMessage(content="苹果多少箱？"),
                AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "one"}]),
                ToolMessage(content="20箱", tool_call_id="one")]
    schemas = [{"type": "function", "function": {"name": "read", "description": "原文" * 100,
                "parameters": {"type": "object"}}}]
    before = deepcopy((messages, schemas))
    value = measure_request(messages, schemas, budget=Budget(context_window=1_000_000),
                            output_cap=1536, phase="routing")
    assert value["legacy_input_estimate"] == input_bound(messages, schemas)
    assert value["legacy_schema_bytes"] == len(json.dumps(schemas, ensure_ascii=False).encode())
    assert value["model_window_tokens"] == 1_000_000
    assert value["estimate_method"] == "serialized_utf8_plus_512_v1"
    assert value["exact_tokens"] is False
    assert value["phase"] == "routing"
    assert "used_percent" not in value and "苹果" not in str(value) and "20箱" not in str(value)
    assert (messages, schemas) == before


def test_repair_instruction_and_tool_schema_change_measurement_and_fingerprint():
    original = [HumanMessage(content="读取报价")]
    repaired = [SystemMessage(content="只修正格式，保留原值与引用"), *original]
    budget = Budget()
    first = measure_request(original, [], budget=budget, output_cap=512, phase="extract")
    second = measure_request(repaired, [], budget=budget, output_cap=512, phase="extract")
    third = measure_request(original, [{"description": "工具"}], budget=budget,
                            output_cap=512, phase="extract")
    for value in (second, third):
        assert value["legacy_input_estimate"] > first["legacy_input_estimate"]
        assert value["request_digest"] != first["request_digest"]


@pytest.mark.parametrize("changes,code", [
    ({"max_prompt": 1}, "context_budget"),
    ({"max_input": 1}, "input_budget"),
    ({"max_output": 100}, "output_budget"),
    ({"max_calls": 0}, "model_budget"),
    ({"cancelled": lambda: True}, "cancelled"),
    ({"seconds": 0}, "deadline"),
])
def test_local_rejection_is_recorded_before_reservation_without_model_usage(changes, code):
    budget, events = Budget(**changes), []
    with pytest.raises(RunStopped, match=code):
        reserve_with_context(budget, [HumanMessage(content="保密采购问题")], [],
                             output_cap=512, phase="extract", events=events,
                             structured_repair=True, semantic_repair=True)
    assert budget.model_calls == budget.unknown_usage_calls == budget.input_reserved == 0
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "rejected" and event["error"] == code
    assert event["provider_called"] is False
    assert event["structured_repair"] is True and event["semantic_repair"] is True
    safe = safe_trace_events(events)[0]["summary"]
    assert safe["context"] == event["context"]
    assert safe["error"] == code and safe["provider_called"] is False
    assert "保密" not in str(safe)


def test_reserved_request_is_not_yet_a_provider_call_and_usage_is_not_fabricated():
    budget, events = Budget(), []
    options, size, event = reserve_with_context(budget, [], [], output_cap=512,
                                               phase="decide", events=events)
    assert event["status"] == "started" and event["provider_called"] is False
    assert "usage" not in event
    budget.record(AIMessage(content=""), size, options["max_output_tokens"])
    assert budget.unknown_usage_calls == 1
    assert budget.summary()["actual_input"] is None
    # A later rejected reservation must not add another unknown response.
    budget.max_prompt = 1
    with pytest.raises(RunStopped):
        reserve_with_context(budget, [], [], output_cap=512, phase="decide", events=events)
    assert budget.unknown_usage_calls == 1 and len(events) == 2


def test_known_usage_reconciles_remaining_legacy_allowance():
    budget, events = Budget(max_input=48000), []
    options, size, _ = reserve_with_context(budget, [], [], output_cap=512,
                                           phase="routing", events=events)
    budget.record(AIMessage(content="", usage_metadata={
        "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
    }), size, options["max_output_tokens"])
    value = measure_request([], [], budget=budget, output_cap=512, phase="edit")
    assert value["legacy_task_input_remaining"] == 47900
    assert value["output_planned_tokens"] == 512


def test_diagnostic_projection_rejects_untrusted_fields_and_preserves_signed_limit():
    measurement = measure_request([], [], budget=Budget(context_window=1), output_cap=512, phase="decide")
    assert measurement["legacy_prompt_limit"] < 0
    measurement.update(prompt="SECRET", phase="SECRET", request_digest="SECRET", exact_tokens="SECRET",
                       legacy_input_estimate=True, legacy_schema_bytes=-1, model_window_tokens=2**80,
                       output_requested_tokens="SECRET", estimate_method="SECRET")
    raw = {"kind": "model", "status": "rejected", "operation": "decide", "error": "context_budget",
           "context": measurement, "provider_called": "SECRET", "prompt": "SECRET"}
    summary = safe_trace_events([raw])[0]["summary"]
    assert "SECRET" not in str(summary)
    assert summary["context"] == {"version": 1, "legacy_prompt_limit": -512,
                                  "legacy_task_input_remaining": None, "output_planned_tokens": 512,
                                  "input_estimate_unit": "utf8_bytes",
                                  "window_fit_method": "one_byte_per_token_allowance_v1",
                                  "safety_reserved_tokens": 1,
                                  "task_input_hard_limit_tokens": None, "task_output_hard_limit_tokens": None}
    assert "provider_called" not in summary
    for invalid in (None, [], "SECRET", {"version": True}, {"version": 2}):
        raw["context"] = invalid
        assert "context" not in safe_trace_events([raw])[0]["summary"]


def test_invalid_phase_is_rejected_without_measuring():
    with pytest.raises(ValueError, match="invalid_context_phase"):
        measure_request([], [], budget=Budget(), output_cap=512, phase="SECRET")


def test_original_a1_diagnostic_is_read_without_new_policy_fields():
    context = {"version": 1, "phase": "routing", "exact_tokens": False,
               "estimate_method": "serialized_utf8_plus_512_v1",
               "legacy_task_input_remaining": 48000, "legacy_prompt_limit": 16000}
    raw = {"kind": "model", "operation": "decide", "status": "completed", "context": context}
    assert safe_trace_events([raw])[0]["summary"]["context"] == context


def test_unlimited_and_explicit_limits_survive_safe_projection_without_percentages():
    for budget in (Budget(), Budget(max_input=80000, max_output=9000)):
        value = measure_request([], [], budget=budget, output_cap=512, phase="decide")
        raw = {"kind": "model", "operation": "decide", "status": "started", "context": value}
        assert safe_trace_events([raw])[0]["summary"]["context"] == value
        assert value["task_input_hard_limit_tokens"] == budget.max_input
        assert value["task_output_hard_limit_tokens"] == budget.max_output
        assert "used_percent" not in value
