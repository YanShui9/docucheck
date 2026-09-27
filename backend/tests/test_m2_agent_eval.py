"""The offline Agent score includes both one and three supplier scenarios."""

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
