"""Reproduce a valid first request whose repair no longer fits the window."""

from importlib import import_module

import pytest
from langchain_core.messages import AIMessage

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.diagnostics import safe_trace_events
from backend.app.agent.dynamic import DynamicAgent
from backend.app.agent.policy import MODE_LIMITS, Budget, input_bound
from backend.app.evidence.repository import EvidenceRepository, load_case
from backend.app.evidence.retrieval import Retriever
from backend.app.providers.base import ProviderError
from backend.tests.test_agent_graph import PACK
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_dynamic_agent import (
    ScriptProvider,
    call,
    definition,
    repository,
)
from backend.tests.test_m5_review_progress import RepeatsFirstSupplier


def rejected_model(events, code="context_budget"):
    found = [event for event in events if event.get("kind") == "model" and event["status"] == "rejected"]
    assert len(found) == 1, events
    assert found[0]["error"] == code
    assert found[0]["provider_called"] is False
    context = found[0]["context"]
    assert context["legacy_input_estimate"] > context["legacy_prompt_limit"]
    return found[0]


def test_assistant_correction_rejection_retains_the_request_and_known_usage():
    budget = Budget()

    class MultipleTools:
        calls = 0

        def decide(self, messages, schemas, **options):
            self.calls += 1
            budget.max_prompt = input_bound(messages, schemas)
            return AIMessage(content="", tool_calls=[
                {"name": "chat", "args": {"answer": "不执行"}, "id": str(i)} for i in range(2)
            ], usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

    provider = MultipleTools()
    result = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider, budget=budget).ask("你好")
    assert result["status"] == "partial" and result["error"] == "context_budget"
    assert provider.calls == 1
    assert result["usage"]["actual_input"] == 100 and result["usage"]["unknown_usage_calls"] == 0
    assert rejected_model(result["events"])["context"]["phase"] == "routing"


def test_dynamic_format_repair_is_measured_before_rejection():
    budget = Budget()

    class InvalidSchema(ScriptProvider):
        def extract(self, messages, schema, **options):
            self.extractions += 1
            budget.max_prompt = input_bound(messages, [schema])
            return call(schema["function"]["name"], {"wrong": True})

    provider = InvalidSchema()
    result = DynamicAgent(repository(), provider, definition(), budget=budget).ask("完整审查")
    assert result["status"] == "partial" and result["error"] == "context_budget"
    assert provider.extractions == 1 and result["usage"]["unknown_usage_calls"] == 0
    assert rejected_model(result["events"])["structured_repair"] is True


def test_evidence_fitting_rejection_records_the_final_oversized_request():
    budget = Budget()
    repo = repository()

    class SearchThenShrink(Decisions):
        def decide(self, messages, schemas, **options):
            reply = super().decide(messages, schemas, **options)
            budget.max_prompt = 1
            return reply

    provider = SearchThenShrink(("search_evidence", {"query": "数量", "supplier": "甲供应商"}))
    result = AssistantAgent(repo, provider, budget=budget, retriever=Retriever(repo)).ask("苹果数量多少？")
    assert result["status"] == "partial" and result["error"] == "context_budget"
    assert len(provider.messages) == budget.model_calls == budget.decisions == 1
    event = rejected_model(result["events"])
    assert event["context"]["phase"] == "evidence"
    assert event["context"]["legacy_input_estimate"] > 1


@pytest.mark.parametrize("tool,code,phase,status", [
    ("read_checklist", "checklist_read_scope_budget", "routing", "partial"),
    ("prepare_changes", "checklist_edit_scope_budget", "edit", "waiting_user"),
])
def test_checklist_fitting_rejection_keeps_original_outcome_and_diagnostics(tool, code, phase, status):
    budget = Budget()
    base = definition()

    class ToolThenShrink(Decisions):
        def decide(self, messages, schemas, **options):
            reply = super().decide(messages, schemas, **options)
            budget.max_prompt = 1
            return reply

    args = ({"criterion_ids": [base.criteria[0].criterion_id], "item_ids": []}
            if tool == "prepare_changes" else {})
    provider = ToolThenShrink((tool, args))
    result = AssistantAgent(repository(), provider, definition=base, confirmed_definition=base,
                            checklist_version_id="confirmed-v1", budget=budget).ask("读取或调整采购要求")
    assert result["status"] == status and result["error"] == code
    assert len(provider.messages) == budget.model_calls == budget.decisions == 1
    event = rejected_model(result["events"], code)
    assert event["context"]["phase"] == phase
    assert safe_trace_events([event])[0]["summary"]["error"] == code


def test_dynamic_semantic_repair_rejection_keeps_its_marker():
    budget = Budget()

    class InvalidValue(ScriptProvider):
        def extract(self, messages, schema, **options):
            response = super().extract(messages, schema, **options)
            budget.max_prompt = input_bound(messages, [schema])
            return response

    provider = InvalidValue(invalid=True)
    result = DynamicAgent(repository(), provider, definition(), budget=budget).ask("完整审查")
    assert result["status"] == "partial" and result["error"] == "context_budget"
    assert provider.extractions == 1 and result["usage"]["unknown_usage_calls"] == 0
    assert rejected_model(result["events"])["semantic_repair"] is True


def test_successful_review_has_one_diagnostic_per_model_call_with_real_usage():
    result = DynamicAgent(repository(), ScriptProvider(), definition()).ask("完整审查")
    assert result["status"] == "completed"
    events = [event for event in result["events"] if event["kind"] == "model"]
    assert len(events) == result["usage"]["model_calls"]
    assert {event["context"]["phase"] for event in events} == {"decide", "extract"}
    for event in events:
        assert event["status"] == "completed" and event["provider_called"] is True
        assert event["usage"]["input_tokens"] == 100
        assert len(event["context"]["request_digest"]) == 64


def test_legacy_format_repair_is_measured_without_replaying_the_provider(monkeypatch):
    budget = Budget(**MODE_LIMITS["thorough"])
    graph = import_module("backend.app.agent.graph")
    monkeypatch.setattr(graph, "Budget", lambda **kwargs: budget)

    class InvalidSchema(RepeatsFirstSupplier):
        extracts = 0

        def extract(self, messages, schema, **options):
            self.extracts += 1
            budget.max_prompt = input_bound(messages, [schema])
            raise ProviderError("invalid_output", detail="tool_schema")

    provider = InvalidSchema()
    result = graph.AgentSession(load_case(PACK), provider, mode="thorough").ask("请完整审查供应商 A 的八项指标")
    assert result.status == "partial" and result.error == "context_budget"
    assert provider.extracts == 1
    # Existing legacy accounting retains the first provider error as unknown;
    # the locally rejected second request must not increase that count.
    assert result.usage["unknown_usage_calls"] == 1
    assert rejected_model(result.events)["structured_repair"] is True
