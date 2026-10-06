"""Follow-up context uses verified handles, not prior answer prose as evidence."""

from dataclasses import replace
from pathlib import Path

from backend.app.agent.graph import AgentSession
from backend.app.evidence.repository import EvidenceRepository, load_case
from backend.tests.test_agent_graph import ScenarioProvider, call

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


def test_verified_result_focus_is_available_to_short_followup() -> None:
    provider = ScenarioProvider()
    session = AgentSession(load_case(PACK), provider)

    first = session.ask("供应商 A 的内存是否达标？")
    assert first.status == "completed"
    session.ask("重量呢？")

    followup_system = provider.contexts[-1][0].content
    assert '"supplier": "vendor-a"' in followup_system
    assert '"field_key": "memory"' in followup_system
    assert "先前核验结果只用于理解指代" in followup_system


def test_short_followup_rejects_a_different_supplier_tool_scope() -> None:
    original = load_case(PACK)
    documents = list(original.documents)
    for document in original.documents:
        if document.role == "requirement":
            continue
        document_id = f"vendor-b-{document.role}"
        parsed = replace(document.parsed, document_id=document_id, blocks=tuple(
            replace(block, document_id=document_id) for block in document.parsed.blocks
        ))
        documents.append(replace(document, document_id=document_id,
                                 supplier="vendor-b", model="DC-B", parsed=parsed))
    repo = EvidenceRepository(original.case_id, original.revision + 1, documents)
    session = AgentSession(repo, ScenarioProvider())
    assert session.ask("供应商 A 的内存是否达标？").status == "completed"

    class WrongSupplier:
        def decide(self, messages, tools, **kwargs):
            return call("extract_facts", {"supplier": "vendor-b",
                                          "field_keys": ["weight"]}, usage=True)

    session.provider = WrongSupplier()
    second = session.ask("重量呢？")
    assert second.status == "failed"
    assert second.error == "supplier_scope"


def test_clarification_question_is_bound_into_next_turn_context() -> None:
    class Clarify:
        def __init__(self):
            self.contexts = []

        def decide(self, messages, tools, **kwargs):
            self.contexts.append(messages)
            return call("request_clarification", {"question": "请指定供应商。"}, usage=True)

    provider = Clarify()
    session = AgentSession(load_case(PACK), provider)
    assert session.ask("请检查").status == "waiting_user"
    assert session.ask("供应商 A").status == "waiting_user"
    assert "上轮等待用户回答的澄清问题：请指定供应商。" in provider.contexts[-1][0].content


def test_unknown_explicit_supplier_requests_clarification_before_model_call() -> None:
    class NoCalls:
        def decide(self, *args, **kwargs):
            raise AssertionError("unknown_supplier_should_be_resolved_before_call")

    session = AgentSession(load_case(PACK), NoCalls())
    result = session.ask("供应商 D 的内存是否达标？")
    assert result.status == "waiting_user"
    assert result.error is None
    assert "vendor-a" in result.answer
    assert session.pending_clarification == result.answer
    assert result.usage["model_calls"] == 0
