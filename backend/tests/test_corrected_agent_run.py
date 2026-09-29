"""A reviewed correction is the source of truth for a new Agent Run."""

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select, update

from backend.app.agent.answer import render_answer
from backend.app.agent.tools import AnswerDraft, ToolExecutor
from backend.app.db.models import (
    agent_runs,
    correction_reviews,
    corrections,
    evidence_blocks,
)
from backend.app.db.run_executor import execute_run
from backend.app.evidence.repository import load_case
from backend.app.rules.memory import evaluate_memory
from backend.tests.test_agent_fields import PACK
from backend.tests.test_agent_graph import ScenarioProvider
from backend.tests.test_correction_recheck import prepared as _prepared
from backend.tests.test_corrections import _request
from backend.tests.test_field_extraction import StorageCandidates


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    yield from _prepared.__wrapped__(tmp_path, monkeypatch)


def _reviewed(prepared):
    client, engine, case_id, source_run_id, set_id, fact_id, _ = prepared
    submitted = client.post(
        f"/api/v1/cases/{case_id}/corrections",
        headers={"Idempotency-Key": "corrected-agent-source"},
        json=_request(case_id, source_run_id, set_id, fact_id),
    )
    assert submitted.status_code == 201, submitted.text
    receipt = submitted.json()
    reviewed = client.post(
        f"/api/v1/cases/{case_id}/corrections/{receipt['correction_id']}/recheck",
    )
    assert reviewed.status_code == 200, reviewed.text
    return client, engine, case_id, receipt, reviewed.json()


def _submit(client, engine, case_id, revision):
    with engine.connect() as connection:
        conversation_id = connection.scalar(select(agent_runs.c.conversation_id))
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "corrected-agent-question"},
        json={"content": "供应商 A 的存储是否达标？", "expected_revision": revision},
    )
    return response


class ReviewedProvider:
    model_id = "scripted-reviewed"
    context_window = 32_768

    def __init__(self, fact_set_id, check_result_id):
        self.fact_set_id = fact_set_id
        self.check_result_id = check_result_id
        self.calls = 0
        self.extractions = 0

    def decide(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            name, args = "extract_facts", {"supplier": "vendor-a", "field_keys": ["storage"]}
        elif self.calls == 2:
            name, args = "evaluate_checks", {"supplier": "vendor-a",
                                             "fact_set_ids": [self.fact_set_id]}
        else:
            name, args = "submit_answer", {"kind": "check",
                                           "check_result_ids": [self.check_result_id]}
        return AIMessage(content="", tool_calls=[{"id": f"reviewed-{self.calls}",
                                                  "name": name, "args": args}],
                         usage_metadata={"input_tokens": 100, "output_tokens": 20,
                                         "total_tokens": 120})

    def extract(self, messages, schema, **kwargs):
        self.extractions += 1
        raise AssertionError("reviewed field must not be re-extracted")


def test_reviewed_correction_allows_run_and_exposes_review_state(prepared):
    client, engine, case_id, receipt, _ = _reviewed(prepared)
    response = _submit(client, engine, case_id, receipt["revision"])
    assert response.status_code == 202, response.text
    run = client.get(f"/api/v1/agent-runs/{response.json()['run_id']}")
    assert run.status_code == 200, run.text
    assert run.json()["correction_id"] == receipt["correction_id"]
    assert run.json()["correction_review_status"] == "needs_review"


def test_corrected_run_uses_both_reviewed_values_without_reextracting(prepared):
    client, engine, case_id, receipt, reviewed = _reviewed(prepared)
    response = _submit(client, engine, case_id, receipt["revision"])
    assert response.status_code == 202, response.text
    with engine.connect() as connection:
        reviewed_set_id = connection.scalar(select(
            correction_reviews.c.fact_set_json,
        ).where(correction_reviews.c.correction_id == receipt["correction_id"]))["fact_set_id"]
    provider = ReviewedProvider(reviewed_set_id,
                                reviewed["check_result"]["check_result_id"])
    report = execute_run(engine, response.json()["run_id"],
                         response.json()["attempt_id"], provider)
    assert report.status == "partial"
    assert provider.extractions == 0
    assert "人工复核" in report.answer_text
    run = client.get(f"/api/v1/agent-runs/{report.run_id}").json()
    assert run["correction_review_status"] == "needs_review"
    assert run["error_code"] is None
    with engine.connect() as connection:
        memory = connection.scalar(select(agent_runs.c.memory_json).where(
            agent_runs.c.id == report.run_id,
        ))
    assert {fact["value"] for fact in memory["fact_sets"][0]["facts"]} == {
        "512", "1000",
    }


def test_changed_review_source_stops_before_model_call(prepared):
    client, engine, case_id, receipt, reviewed = _reviewed(prepared)
    response = _submit(client, engine, case_id, receipt["revision"])
    assert response.status_code == 202, response.text
    with engine.begin() as connection:
        connection.execute(update(evidence_blocks).where(
            evidence_blocks.c.id == reviewed["corrected_fact"]["evidence_refs"][0],
        ).values(source_text="标称 SSD 2 TB"))
    provider = ReviewedProvider("unused", "unused")
    report = execute_run(engine, response.json()["run_id"],
                         response.json()["attempt_id"], provider)
    assert report.status == "partial"
    assert report.error_code == "correction_source_changed"
    assert provider.calls == 0


def test_changed_review_result_stops_before_model_call(prepared):
    client, engine, case_id, receipt, _ = _reviewed(prepared)
    response = _submit(client, engine, case_id, receipt["revision"])
    assert response.status_code == 202, response.text
    with engine.begin() as connection:
        saved = connection.scalar(select(correction_reviews.c.check_result_json).where(
            correction_reviews.c.correction_id == receipt["correction_id"],
        ))
        connection.execute(update(correction_reviews).where(
            correction_reviews.c.correction_id == receipt["correction_id"],
        ).values(check_result_json={**saved, "business_status": "满足"}))
    provider = ReviewedProvider("unused", "unused")
    report = execute_run(engine, response.json()["run_id"],
                         response.json()["attempt_id"], provider)
    assert report.status == "partial"
    assert report.error_code == "correction_source_changed"
    assert provider.calls == 0


def test_changed_review_source_rejects_new_run_submission(prepared):
    client, engine, case_id, receipt, reviewed = _reviewed(prepared)
    with engine.begin() as connection:
        connection.execute(update(evidence_blocks).where(
            evidence_blocks.c.id == reviewed["corrected_fact"]["evidence_refs"][0],
        ).values(source_text="标称 SSD 2 TB"))
    response = _submit(client, engine, case_id, receipt["revision"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "correction_source_changed"


def test_malformed_correction_audit_stops_before_model_call(prepared):
    client, engine, case_id, receipt, _ = _reviewed(prepared)
    response = _submit(client, engine, case_id, receipt["revision"])
    assert response.status_code == 202, response.text
    with engine.begin() as connection:
        connection.execute(update(corrections).where(
            corrections.c.id == receipt["correction_id"],
        ).values(corrected_candidate_json={"invalid": "payload"}))
    provider = ReviewedProvider("unused", "unused")
    report = execute_run(engine, response.json()["run_id"],
                         response.json()["attempt_id"], provider)
    assert report.status == "partial"
    assert report.error_code == "correction_source_changed"
    assert provider.calls == 0


def test_reviewed_legacy_memory_can_join_eight_field_extraction():
    repo = load_case(PACK)
    tools = ToolExecutor(repo)
    provider = ScenarioProvider()
    tools.execute("extract_facts", {"supplier": "vendor-a",
                                    "field_keys": ["memory"]},
                  lambda operation, messages, schema: provider.extract(messages, schema))
    memory = next(iter(tools.fact_sets.values()))
    result = evaluate_memory(repo, "vendor-a", list(memory.facts),
                             scan_complete=memory.complete)
    tools.bind_review(memory, result, "needs_review")
    extracted = tools.execute("extract_facts", {"supplier": "vendor-a",
                                               "field_keys": ["memory", "storage"]},
                              StorageCandidates())
    sets = extracted["data"]["fact_sets"]
    assert {item["field_key"] for item in sets} == {"memory", "storage"}
    checks = tools.execute("evaluate_checks", {"supplier": "vendor-a",
                                              "fact_set_ids": [item["fact_set_id"]
                                                               for item in sets]}, None)
    assert {item["field_key"] for item in checks["data"]["check_results"]} == {
        "memory", "storage",
    }


def test_review_notice_remains_visible_even_when_rule_says_met():
    repo = load_case(PACK)
    tools = ToolExecutor(repo)
    extracted = tools.execute("extract_facts", {"supplier": "vendor-a",
                                                "field_keys": ["storage"]},
                              StorageCandidates())
    fact_set = tools.fact_sets[extracted["data"]["fact_sets"][0]["fact_set_id"]]
    evaluated = tools.execute("evaluate_checks", {"supplier": "vendor-a",
                                                 "fact_set_ids": [fact_set.fact_set_id]},
                              None)
    result = tools.results[evaluated["data"]["check_result_ids"][0]]
    assert result.business_status == "满足"
    tools.bind_review(fact_set, result, "needs_review")
    answer, status = render_answer(
        AnswerDraft(kind="check", check_result_ids=[result.check_result_id]),
        tools, "供应商 A 的存储是否达标？",
    )
    assert status == "completed"
    assert "存储：满足" in answer
    assert "原值与修正值均有原文支持，仍需人工复核" in answer
