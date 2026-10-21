"""Growing tool observations must not retain dispensable answer history."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.app.agent import context
from backend.app.agent.policy import Budget, RunStopped, input_bound


def test_dynamic_history_trimming_preserves_question_handles_and_tool_pair():
    system = SystemMessage(content="server scope and policy")
    handles = SystemMessage(content="VERIFIED_CHECK_RESULTS=bound handles")
    older = [HumanMessage(content="old question"), AIMessage(content="old answer " * 1000)]
    recent = [HumanMessage(content="recent question"), AIMessage(content="recent answer")]
    question = HumanMessage(content="供应商 A 的内存冲突根据哪两处来源判断？")
    tool = AIMessage(content="", tool_calls=[{"id": "source", "name": "read_source",
                                             "args": {"evidence_ids": ["bound-source"]}}])
    observation = ToolMessage(content="verified source " * 200, tool_call_id="source")
    messages = [system, handles, *older, *recent, question, tool, observation]
    before = list(messages)
    assert input_bound(messages, []) > 6000
    trimmed, dropped = context.fit_decision_history(messages, [], max_prompt=6000, context_window=10000)
    assert dropped == 1
    assert trimmed == [system, handles, *recent, question, tool, observation]
    assert input_bound(trimmed, []) <= 6000
    assert messages == before


def test_current_question_or_observation_is_not_removed_to_hide_an_oversize_request():
    messages = [SystemMessage(content="mandatory policy"), HumanMessage(content="current question"),
                AIMessage(content="", tool_calls=[{"id": "s", "name": "read_source", "args": {}}]),
                ToolMessage(content="source " * 2000, tool_call_id="s")]
    trimmed, dropped = context.fit_decision_history(messages, [], max_prompt=6000, context_window=10000)
    assert trimmed == messages and dropped == 0
    with pytest.raises(RunStopped, match="context_budget"):
        Budget(max_prompt=6000, context_window=10000).reserve(trimmed, [])


def test_history_uses_context_output_headroom_without_dropping_system_guidance():
    system = SystemMessage(content="mandatory policy")
    guide = SystemMessage(content="authorized next step")
    question = HumanMessage(content="current question")
    old = [HumanMessage(content="old question"), AIMessage(content="answer " * 500)]
    messages = [system, guide, *old, question]
    trimmed, dropped = context.fit_decision_history(messages, [], max_prompt=16000, context_window=2500)
    assert dropped == 1 and trimmed == [system, guide, question]
    assert input_bound(trimmed, []) <= 2500 - 512


def test_source_followup_after_large_history_fits_the_configured_prompt_budget():
    import json

    from backend.app.agent.graph import AgentSession
    from backend.app.evidence.repository import load_case
    from backend.tests.test_agent_fields import PACK_THREE
    from backend.tests.test_agent_graph import ScenarioProvider

    question = "供应商 A 的内存冲突根据哪两处来源判断？"

    class ReadsKnownSources(ScenarioProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.prompts = []

        def decide(self, messages, tools, **kwargs):
            self.prompts.append(messages)
            assert input_bound(messages, tools) <= 16000
            self.calls += 1
            if self.calls == 1:
                name, args = "get_check_results", {"supplier": "vendor-a", "field_keys": ["memory"]}
            elif self.calls == 2:
                cached = json.loads(messages[-1].content)["data"]["check_results"][0]
                name, args = "read_source", {"evidence_ids": cached["evidence_refs"]}
            else:
                name, args = "submit_answer", {"kind": "check", "check_result_ids": [verified.check_result_id]}
            return AIMessage(content="", tool_calls=[{"id": f"follow-{self.calls}", "name": name, "args": args}],
                             usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

    provider = ReadsKnownSources()
    provider.context_window = 17380  # 15999 input allowance after output and safety.
    session = AgentSession(load_case(PACK_THREE), provider)
    extraction = session.tools.execute("extract_facts", {"supplier": "vendor-a"},
                                       lambda op, messages, schema: provider.extract(messages, schema))
    session.tools.execute("evaluate_checks", {"supplier": "vendor-a", "fact_set_id": extraction["data"]["fact_set_id"]}, None)
    verified = next(iter(session.tools.results.values()))
    base = context.assemble(session.repo, [], question, session.schemas, 32768)
    history_chars = 16000 - input_bound(base, session.schemas) - 300
    assert history_chars > 0
    session.history = [(HumanMessage(content="先前完整审查"), AIMessage(content="H" * history_chars))]
    result = session.ask(question)
    assert result.status == "completed", (result.error, result.usage)
    assert provider.calls == 3
    assert "vendor-a · 内存：资料冲突" in result.answer and "vendor-b ·" not in result.answer
    assert "vendor-a-quote.xlsx" in result.answer and "vendor-a-spec.pdf" in result.answer
    assert any(e.get("history_turns_dropped", 0) > 0 for e in result.events)
    assert len(session.history) == 2
