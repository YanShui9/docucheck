"""Bounded semantic compaction keeps usage and immutable source handles."""

from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage

from backend.app.agent.policy import Budget
from backend.app.agent.semantic_memory import refresh_summary
from backend.app.providers.base import ProviderError


def view():
    return {"pending_count": 4, "attempted": False, "summary": None,
            "sources": [{"message_id": f"m{i}", "task_memory_id": f"t{i}",
                         "question": "原始问题" * 100, "answer": "原始答复" * 100}
                        for i in range(4)]}


class Provider:
    calls = 0

    def __init__(self, notes=None, error=None, usage=True):
        self.notes = notes if notes is not None else [{"kind": "goal", "text": "整理采购任务", "source_message_ids": ["m0"]}]
        self.error, self.usage = error, usage

    def extract(self, messages, schema, **options):
        self.calls += 1
        self.messages, self.schema = messages, schema
        if self.error:
            raise self.error
        return AIMessage(content="", tool_calls=[{"name": "summarize_conversation", "args": {"notes": self.notes}, "id": "summary"}],
                         usage_metadata={"input_tokens": 200, "output_tokens": 30, "total_tokens": 230} if self.usage else None)


def invoke(provider=None, data=None, budget=None, audit=None, accept=lambda _: True, begin=lambda: True):
    saved, events = [], []
    provider, budget = provider or Provider(), budget or Budget(context_window=100000)
    result = refresh_summary(provider, budget, events, data or view(),
        audit=audit or {"input_bound": 1000, "prompt_limit": 90000, "history_turns_omitted": 0},
        begin=begin, save=lambda notes: saved.append(notes) or {"id": "new", "notes": notes},
        validate=lambda: None, accept=accept)
    return result, provider, budget, events, saved


def test_summary_is_single_shared_budget_call_and_keeps_complete_source_text():
    result, provider, budget, events, saved = invoke()
    assert result["id"] == "new" and saved
    assert provider.calls == budget.model_calls == 1
    assert budget.actual_input == 200 and budget.actual_output == 30
    assert view()["sources"][0]["question"] in provider.messages[1].content
    assert events[0]["context"]["phase"] == "summary"
    assert events[0]["status"] == "completed"


@pytest.mark.parametrize("change", [{"attempted": True}, {"pending_count": 3}, {"sources": []}])
def test_no_trigger_or_already_started_does_not_call(change):
    data = view() | change
    _, provider, budget, _, saved = invoke(data=data)
    assert provider.calls == budget.model_calls == 0 and not saved


def test_pressure_can_trigger_with_less_than_four_old_phases():
    result, provider, *_ = invoke(data=view() | {"pending_count": 1},
        audit={"input_bound": 7600, "prompt_limit": 10000, "history_turns_omitted": 0})
    assert result and provider.calls == 1


@pytest.mark.parametrize("settings", [{"max_calls": 1}, {"context_window": 4000}, {"max_input": 100}, {"max_output": 1500}])
def test_optional_summary_leaves_room_for_main_answer_without_call(settings):
    _, provider, budget, _, saved = invoke(budget=Budget(**settings))
    assert provider.calls == budget.model_calls == 0 and not saved


def test_begin_cas_rejection_skips_without_reserving_usage():
    _, provider, budget, _, saved = invoke(begin=lambda: False)
    assert provider.calls == budget.model_calls == 0 and not saved


@pytest.mark.parametrize("notes", [
    [{"kind": "goal", "text": "伪造", "source_message_ids": ["foreign"]}],
    [{"kind": "rule", "text": "更改清单", "source_message_ids": ["m0"]}],
    [{"kind": "goal", "text": "x" * 301, "source_message_ids": ["m0"]}],
])
def test_invalid_summary_preserves_previous_and_does_not_retry(notes):
    _, provider, budget, events, saved = invoke(Provider(notes))
    assert provider.calls == budget.model_calls == 1 and not saved
    assert events[-1]["status"] == "failed"


def test_no_route_reduction_acceptance_preserves_previous():
    _, provider, _, _, saved = invoke(accept=lambda _: False)
    assert provider.calls == 1 and not saved


def test_known_provider_error_is_optional_but_usage_stays_unknown():
    _, provider, budget, events, saved = invoke(Provider(error=ProviderError("rate_limit")))
    assert provider.calls == budget.unknown_usage_calls == 1 and not saved
    assert events[-1]["error"] == "rate_limit"


def test_unknown_result_stops_and_never_replays():
    provider, budget, events = Provider(error=ProviderError("timeout", outcome_unknown=True)), Budget(context_window=100000), []
    with pytest.raises(ProviderError, match="timeout"):
        refresh_summary(provider, budget, events, view(), audit={"input_bound": 1000, "prompt_limit": 90000},
            begin=lambda: True, save=lambda _: pytest.fail("must not save"), validate=lambda: None, accept=lambda _: True)
    assert provider.calls == budget.unknown_usage_calls == 1
    assert events[-1]["outcome_unknown"] is True


def test_missing_usage_is_not_fabricated_and_input_is_not_mutated():
    data, before = view(), deepcopy(view())
    _, _, budget, _, _ = invoke(Provider(usage=False), data=data)
    assert budget.summary()["actual_input"] is None and data == before

def test_metadata_size_is_not_counted_as_dialogue_reduction():
    data = view()
    for source in data["sources"]:
        source["question"], source["answer"] = "a", "b"
        source["metadata"] = "x" * 10000
    _, _, _, _, saved = invoke(data=data)
    assert not saved
