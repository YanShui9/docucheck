"""Configured windows and explicit run quotas have different meanings."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.policy import MODE_LIMITS, Budget, RunStopped, input_bound


def reply(input_tokens=20000, output_tokens=2000):
    return AIMessage(content="", usage_metadata={
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    })


def test_large_configured_window_accepts_more_than_old_fixed_prompt_limit():
    budget = Budget(context_window=1_000_000)
    options, size = budget.reserve([HumanMessage(content="采购要求" * 3000)], [], output_cap=4096)
    assert size > 16000
    assert options["max_output_tokens"] == 4096
    assert budget.prompt_limit(4096) == 945904


def test_window_includes_output_and_rounds_safety_margin_up():
    budget = Budget(context_window=10001)
    assert budget.prompt_limit(512) == 8988
    messages = [HumanMessage(content="a")]
    budget.context_window = input_bound(messages, []) + 512
    with pytest.raises(RunStopped, match="context_budget"):
        budget.reserve(messages, [])
    assert budget.model_calls == budget.input_reserved == 0


@pytest.mark.parametrize("known", [True, False])
def test_cumulative_usage_can_cross_old_input_and_output_caps_without_explicit_quota(known):
    budget = Budget(context_window=1_000_000)
    for _ in range(5):
        options, size = budget.reserve([HumanMessage(content="a" * 20000)], [], output_cap=4096)
        budget.record(reply() if known else AIMessage(content=""), size, options["max_output_tokens"])
    assert budget.input_reserved > 48000 and budget.output_reserved > 6000
    if known:
        assert budget.summary()["actual_input"] == 100000
        assert budget.summary()["actual_output"] == 10000
    else:
        assert budget.summary()["actual_input"] is None
        assert budget.summary()["unknown_usage_calls"] == 5


@pytest.mark.parametrize("mode", ["standard", "thorough"])
def test_modes_do_not_impose_hidden_window_or_cumulative_quotas(mode):
    budget = Budget(**MODE_LIMITS[mode], context_window=1_000_000)
    assert budget.max_input is None and budget.max_output is None
    assert budget.prompt_limit(512) == 949488


def test_explicit_input_quota_rejects_before_call_but_does_not_shrink_window():
    budget = Budget(max_input=600, context_window=1_000_000)
    before = budget.prompt_limit(512)
    with pytest.raises(RunStopped, match="input_budget"):
        budget.reserve([HumanMessage(content="x" * 1000)], [])
    assert budget.prompt_limit(512) == before
    assert budget.model_calls == 0


def test_explicit_output_quota_caps_each_call_and_rejects_when_exhausted():
    budget = Budget(max_output=700)
    options, size = budget.reserve([], [], output_cap=1000)
    assert options["max_output_tokens"] == 700
    budget.record(reply(100, 650), size, 700)
    with pytest.raises(RunStopped, match="output_budget"):
        budget.reserve([], [])
    assert budget.model_calls == 1


@pytest.mark.parametrize("changes,usage,code", [
    ({"max_input": 600}, reply(601, 10), "input_budget"),
    ({"max_output": 512}, reply(100, 513), "output_budget"),
])
def test_reported_actual_usage_over_explicit_quota_is_preserved(changes, usage, code):
    budget = Budget(**changes)
    options, size = budget.reserve([], [])
    with pytest.raises(RunStopped, match=code):
        budget.record(usage, size, options["max_output_tokens"])
    assert budget.summary()["actual_input"] == usage.usage_metadata["input_tokens"]


@pytest.mark.parametrize("changes", [
    {"context_window": True}, {"context_window": 0}, {"context_window": -1},
    {"max_input": 0}, {"max_input": True}, {"max_input": 1.5},
    {"max_output": -1}, {"max_output": "6000"}, {"max_prompt": 0},
    {"safety_percent": True}, {"safety_percent": 100}, {"safety_percent": -1},
])
def test_invalid_capacity_or_explicit_quota_fails_closed(changes):
    with pytest.raises(ValueError):
        Budget(**changes)


@pytest.mark.parametrize("changes,code", [
    ({"max_calls": 0}, "model_budget"), ({"seconds": 0}, "deadline"),
    ({"cancelled": lambda: True}, "cancelled"),
])
def test_other_execution_guards_still_stop_calls(changes, code):
    budget = Budget(context_window=1_000_000, **changes)
    with pytest.raises(RunStopped, match=code):
        budget.reserve([], [])
    assert budget.model_calls == 0
