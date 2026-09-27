"""Execution modes have distinct bounded decision budgets."""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from backend.app.agent.graph import AgentSession
from backend.app.evidence.repository import load_case
from backend.app.providers import ProviderError
from evals.m2_agent import ScriptedProvider

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


class NineDecisions:
    def __init__(self) -> None:
        self.decisions = 0

    def decide(self, messages, tools, **kwargs):
        self.decisions += 1
        name = "list_case_sources" if self.decisions <= 8 else "request_clarification"
        args = {} if name == "list_case_sources" else {"question": "请确认审查范围。"}
        return AIMessage(
            content="",
            tool_calls=[{"id": f"step-{self.decisions}", "name": name, "args": args}],
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    def extract(self, messages, schema, **kwargs):
        raise AssertionError("this scenario should not extract")


def test_thorough_mode_allows_more_bounded_decisions_than_standard() -> None:
    repo = load_case(PACK)
    standard = AgentSession(repo, NineDecisions(), mode="standard").ask("审查资料")
    thorough = AgentSession(repo, NineDecisions(), mode="thorough").ask("审查资料")

    assert standard.status == "partial"
    assert standard.error == "decision_budget"
    assert standard.usage["decisions"] == 8
    assert thorough.status == "waiting_user"
    assert thorough.context.mode == "thorough"
    assert thorough.usage["decisions"] == 9


def test_invalid_mode_is_rejected_before_running_agent() -> None:
    with pytest.raises(ValueError, match="invalid_mode"):
        AgentSession(load_case(PACK), NineDecisions(), mode="unbounded")


def test_thorough_full_review_reserves_enough_output_for_field_candidates() -> None:
    class LimitSensitive(ScriptedProvider):
        def __init__(self) -> None:
            super().__init__(("vendor-a",))
            self.limits: list[tuple[str, int]] = []

        def extract(self, messages, schema, **kwargs):
            name = schema["function"]["name"]
            limit = kwargs["max_output_tokens"]
            self.limits.append((name, limit))
            if name == "FieldCandidates" and limit < 1024:
                raise ProviderError("invalid_output")
            return super().extract(messages, schema, **kwargs)

    provider = LimitSensitive()
    result = AgentSession(load_case(PACK), provider, mode="thorough").ask(
        "完整审查供应商 A 的八项指标")

    assert result.status == "completed", result.error
    assert {limit for name, limit in provider.limits if name == "MemoryCandidates"} == {512}
    assert {limit for name, limit in provider.limits if name == "FieldCandidates"} == {1536}
