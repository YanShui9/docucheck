"""Explicit full reviews have the same progress guard at any supplier count."""
import re

import pytest

from backend.app.agent.graph import AgentSession
from backend.app.evidence.repository import load_case
from backend.app.providers import ProviderError
from backend.tests.test_agent_fields import PACK, PACK_THREE, RepeatsFirstSupplier


@pytest.mark.parametrize("pack,question,supplier", [
    (PACK, "请完整审查全部供应商的八项指标，并列出来源", "vendor-a"),
    (PACK, "请完整审查供应商 A 的八项指标", "vendor-a"),
    (PACK_THREE, "请完整审查供应商 B 的八项指标", "vendor-b"),
])
def test_single_requested_supplier_progresses_without_looping_or_widening_scope(pack, question, supplier):
    session = AgentSession(load_case(pack), RepeatsFirstSupplier(), mode="thorough")
    result = session.ask(question)
    assert result.status == "completed", (result.error, result.usage)
    rows = re.findall(r"^(vendor-[abc]) · ", result.answer, re.MULTILINE)
    assert rows == [supplier] * 8
    assert [event["tool"] for event in result.events if event["kind"] == "tool"] == [
        "list_case_sources", "extract_facts", "evaluate_checks", "submit_answer",
    ]
    assert result.usage["decisions"] == 4
    assert {fact_set.supplier for fact_set in session.tools.fact_sets.values()} == {supplier}


def test_partial_source_coverage_stays_partial_after_only_one_bounded_full_review_retry():
    from backend.app.field_contract import FieldKey

    repo = load_case(PACK)
    missing = next(b.evidence_id for b in repo.blocks if b.role == "spec" and "标称 SSD 1 TB" in b.text)

    class MissingStorage(RepeatsFirstSupplier):
        def extract(self, messages, schema, **kwargs):
            response = super().extract(messages, schema, **kwargs)
            if schema["function"]["name"] == "FieldCandidates":
                response.tool_calls[0]["args"]["facts"] = [
                    f for f in response.tool_calls[0]["args"]["facts"] if missing not in f["evidence_refs"]
                ]
            return response

    session = AgentSession(repo, MissingStorage(), mode="thorough")
    result = session.ask("请完整审查供应商 A 的八项指标")
    assert result.status == "partial" and result.error is None
    assert "vendor-a · 存储：处理未完成" in result.answer
    assert result.usage["decisions"] == 5
    assert len([e for e in result.events if e.get("tool") == "extract_facts"]) == 2
    assert len(session.tools.results) == len(FieldKey)


@pytest.mark.parametrize("pack,question,expected_rows,last_decision", [
    (PACK, "请完整审查供应商 A 的八项指标", 8, 3),
    (PACK_THREE, "请完整审查供应商 B 的八项指标", 8, 3),
    (PACK_THREE, "请完整审查全部供应商的八项指标", 24, 7),
])
def test_evaluated_full_review_finalizes_without_another_model_request(pack, question, expected_rows, last_decision):
    class TruncatesFinalDecision(RepeatsFirstSupplier):
        def decide(self, messages, tools, **kwargs):
            if self.step >= last_decision:
                raise ProviderError("invalid_output", detail="output_limit")
            return super().decide(messages, tools, **kwargs)

    provider = TruncatesFinalDecision()
    session = AgentSession(load_case(pack), provider, mode="thorough")
    result = session.ask(question)
    assert result.status == "completed", (result.error, result.usage)
    assert len(re.findall(r"^vendor-[abc] · ", result.answer, re.MULTILINE)) == expected_rows
    assert provider.step == last_decision
    assert result.usage["unknown_usage_calls"] == 0
    policy = [event for event in result.events if event["kind"] == "policy"]
    assert len(policy) == 1 and policy[0]["action"] == "submit_answer"
    assert policy[0]["reason"] == "full_review_evaluated"
    assert result.events[-1]["tool"] == "submit_answer"


def test_model_failure_before_full_review_evaluation_is_not_finalized():
    class FailsExtraction(RepeatsFirstSupplier):
        def extract(self, messages, schema, **kwargs):
            raise ProviderError("invalid_output", detail="output_limit")

    result = AgentSession(load_case(PACK), FailsExtraction(), mode="thorough").ask(
        "请完整审查供应商 A 的八项指标")
    assert result.status == "failed" and result.error == "invalid_output"
    assert not any(event.get("action") == "submit_answer" for event in result.events)
    assert not any(event.get("tool") == "submit_answer" for event in result.events)


def test_cancellation_after_evaluation_prevents_policy_submission(monkeypatch):
    session = AgentSession(load_case(PACK), RepeatsFirstSupplier(), mode="thorough")
    execute = session.tools.execute

    def cancel_after_evaluation(name, arguments, model_call):
        result = execute(name, arguments, model_call)
        if name == "evaluate_checks":
            session.cancel()
        return result

    monkeypatch.setattr(session.tools, "execute", cancel_after_evaluation)
    result = session.ask("请完整审查供应商 A 的八项指标")
    assert result.status == "cancelled" and result.error == "cancelled"
    assert not any(event.get("tool") == "submit_answer" for event in result.events)


def test_one_known_extraction_schema_error_gets_one_bounded_repair():
    class RepairsSchema(RepeatsFirstSupplier):
        def __init__(self):
            super().__init__()
            self.extract_calls = 0
            self.saw_guidance = False

        def extract(self, messages, schema, **kwargs):
            self.extract_calls += 1
            if self.extract_calls == 1:
                raise ProviderError("invalid_output", detail="tool_schema")
            if self.extract_calls == 2:
                self.saw_guidance = any("schema" in str(m.content) and "纠正" in str(m.content) for m in messages)
            return super().extract(messages, schema, **kwargs)

    provider = RepairsSchema()
    result = AgentSession(load_case(PACK), provider, mode="thorough").ask("请完整审查供应商 A 的八项指标")
    assert result.status == "completed", (result.error, result.usage)
    assert provider.saw_guidance
    failures = [e for e in result.events if e.get("failure_detail") == "tool_schema"]
    assert len(failures) == 1 and failures[0]["outcome_unknown"] is False
    assert result.usage["unknown_usage_calls"] == 1
    assert result.usage["actual_input"] is None and result.usage["actual_output"] is None
    assert len([e for e in result.events if e.get("structured_repair") is True]) == 1


@pytest.mark.parametrize("code,detail,outcome_unknown,expected_calls", [
    ("invalid_output", "tool_schema", False, 2),
    ("invalid_output", "tool_schema", True, 1),
    ("invalid_output", "output_limit", False, 1),
    ("authentication_error", None, False, 1),
    ("timeout", None, True, 1),
])
def test_schema_repair_is_bounded_and_does_not_replay_other_or_unknown_errors(code, detail, outcome_unknown, expected_calls):
    class AlwaysFails(RepeatsFirstSupplier):
        def __init__(self):
            super().__init__()
            self.extract_calls = 0

        def extract(self, messages, schema, **kwargs):
            self.extract_calls += 1
            raise ProviderError(code, detail=detail, outcome_unknown=outcome_unknown)

    provider = AlwaysFails()
    result = AgentSession(load_case(PACK), provider, mode="thorough").ask("请完整审查供应商 A 的八项指标")
    assert result.status == "failed" and result.error == code
    assert provider.extract_calls == expected_calls
    assert not any(event.get("tool") == "submit_answer" for event in result.events)


def test_schema_repair_cannot_exceed_the_shared_model_call_budget(monkeypatch):
    from backend.app.agent.policy import MODE_LIMITS

    class FailsSchema(RepeatsFirstSupplier):
        extract_calls = 0

        def extract(self, messages, schema, **kwargs):
            self.extract_calls += 1
            raise ProviderError("invalid_output", detail="tool_schema")

    monkeypatch.setitem(MODE_LIMITS, "thorough", MODE_LIMITS["thorough"] | {"max_calls": 3})
    provider = FailsSchema()
    result = AgentSession(load_case(PACK), provider, mode="thorough").ask("供应商 A 的内存是否达标？")
    assert result.status == "partial" and result.error == "model_budget"
    assert result.usage["model_calls"] == 3 and provider.extract_calls == 1


def test_cancellation_between_schema_attempts_prevents_a_second_request():
    class CancelSchema(RepeatsFirstSupplier):
        extract_calls = 0

        def extract(self, messages, schema, **kwargs):
            self.extract_calls += 1
            session.cancel()
            raise ProviderError("invalid_output", detail="tool_schema")

    provider = CancelSchema()
    session = AgentSession(load_case(PACK), provider, mode="thorough")
    result = session.ask("供应商 A 的内存是否达标？")
    assert result.status == "cancelled" and result.error == "cancelled"
    assert provider.extract_calls == 1
