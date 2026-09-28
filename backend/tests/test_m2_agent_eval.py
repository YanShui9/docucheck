"""The offline Agent score includes both one and three supplier scenarios."""

from dataclasses import replace

from backend.app.agent.graph import AgentSession
from evals import m2_agent
from evals.m2_agent import evaluate


def test_scripted_agent_eval_reports_actual_denominators() -> None:
    report = evaluate()
    assert report["provider_kind"] == "scripted_no_api"
    assert report["real_model_token_usage"] is None
    assert report["agent_task_success"] == {"numerator": 2, "denominator": 2}
    assert report["status_accuracy"] == {"numerator": 32, "denominator": 32}
    assert all(item["citation_errors"]["numerator"] == 0
               and item["citation_errors"]["denominator"] > 0
               for item in report["scenarios"])


def test_agent_eval_scores_final_visible_answer_instead_of_cached_checks(monkeypatch):
    class TruncatedAnswer(AgentSession):
        def ask(self, question):
            result = super().ask(question)
            return replace(result, answer=result.answer.split("\n\n", 1)[0])

    monkeypatch.setattr(m2_agent, "AgentSession", TruncatedAnswer)
    report = m2_agent.evaluate()

    assert report["status_accuracy"]["numerator"] == 2
    assert report["agent_task_success"]["numerator"] == 0
    assert all(item["citation_errors"]["numerator"] > 0 for item in report["scenarios"])


def test_agent_eval_command_returns_failure_when_score_regresses(monkeypatch, capsys):
    report = evaluate()
    report["status_accuracy"]["numerator"] -= 1
    monkeypatch.setattr(m2_agent, "evaluate", lambda: report)

    assert m2_agent.main([]) == 1
    capsys.readouterr()
