"""Durable manual alternatives survive conversational routing and fresh reviews."""

import pytest
from sqlalchemy import select, update

from backend.app.criteria.facts import FactCandidate
from backend.app.db.dynamic_repository import DynamicRepository
from backend.app.db.models import dynamic_runs
from backend.app.db.run_repository import RepositoryError, RunRepository
from backend.tests import test_dynamic_workflow
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_assistant_workflow import run, send
from backend.tests.test_dynamic_agent import ScriptProvider

dynamic_case = test_dynamic_workflow.dynamic_case
ingestion_case = test_dynamic_workflow.ingestion_case


def corrected(case, monkeypatch, old_rule):
    from backend.app.agent import dynamic

    confirmed, _ = test_dynamic_workflow.confirm(case)
    receipt = send(case, "完整审查", key="manual-origin")
    with monkeypatch.context() as patch:
        patch.setattr(dynamic, "BINDING_RULE_VERSION", old_rule)
        assert run(case, receipt, ScriptProvider()).status == "completed"
    client = case[0]
    prefix = f"/api/v1/agent-runs/{receipt['run_id']}"
    facts = client.get(prefix + "/facts").json()
    old = next(g for g in facts["fact_sets"] if g["supplier"] == "vendor-a")["facts"][0]
    candidate = {k: old[k] for k in FactCandidate.model_fields} | {"value": "8", "unit": "箱"}
    response = client.post(prefix + "/fact-corrections", headers={"Idempotency-Key": "source-alternative"},
        json={"fact_id": old["fact_id"], "candidate": candidate, "reason": "保留同源8箱备选",
              "expected_revision": 3, "expected_payload_digest": facts["payload_digest"]})
    assert response.status_code == 201, response.text
    return receipt, confirmed


@pytest.mark.parametrize("dynamic_case", ["苹果 数量12箱或8箱"], indirect=True)
@pytest.mark.parametrize("old_rule", ["dynamic-rules-v5", "dynamic-rules-v6"])
@pytest.mark.parametrize("assistant", [True, False])
def test_fresh_review_keeps_source_supported_manual_alternative_after_chat(dynamic_case, monkeypatch, old_rule, assistant):
    original, _ = corrected(dynamic_case, monkeypatch, old_rule)
    chat = send(dynamic_case, "谢谢", key="intervening-chat")
    assert run(dynamic_case, chat, Decisions(("chat", {"answer": "请继续核对原文。"}))).status == "completed"
    if assistant:
        receipt = send(dynamic_case, "重新完整审查", key="manual-carry-review")
    else:
        rid = test_dynamic_workflow.message(dynamic_case, key="manual-carry-direct")
        with dynamic_case[1].connect() as connection:
            from backend.app.db.models import agent_runs
            attempt = connection.scalar(select(agent_runs.c.active_attempt_id).where(agent_runs.c.id == rid))
        receipt = {"run_id": rid, "attempt_id": attempt}
    assert run(dynamic_case, receipt, ScriptProvider()).status == "completed"
    _, payload, _ = DynamicRepository(dynamic_case[1]).verified(receipt["run_id"])
    assert payload.get("needs_review") is True
    assert {f["value"] for f in payload["facts"] if f["supplier"] == "vendor-a"} == {"12", "8"}
    result = dynamic_case[0].get(f"/api/v1/agent-runs/{receipt['run_id']}/checks").json()
    assert not any(c["is_formal"] for c in result["checks"])
    assert DynamicRepository(dynamic_case[1]).verified(original["run_id"])[1]["rule_version"] == old_rule


@pytest.mark.parametrize("dynamic_case", ["苹果 数量12箱或8箱"], indirect=True)
def test_tampered_manual_report_stops_before_new_extraction(dynamic_case, monkeypatch):
    original, _ = corrected(dynamic_case, monkeypatch, "dynamic-rules-v6")
    with dynamic_case[1].begin() as connection:
        connection.execute(update(dynamic_runs).where(dynamic_runs.c.run_id == original["run_id"]).values(payload_digest="b"*64))
    receipt = send(dynamic_case, "重新审查", key="manual-tamper")
    provider = ScriptProvider()
    report = run(dynamic_case, receipt, provider)
    assert report.status == "partial" and report.error_code == "dynamic_source_changed"
    assert provider.extractions == 0


@pytest.mark.parametrize("dynamic_case", ["苹果 数量12箱或8箱"], indirect=True)
def test_new_conversation_cannot_import_another_conversations_manual_state(dynamic_case, monkeypatch):
    corrected(dynamic_case, monkeypatch, "dynamic-rules-v6")
    client, engine, case_id, snapshot, _ = dynamic_case
    conversation = client.post(f"/api/v1/cases/{case_id}/conversations").json()["id"]
    new_case = (client, engine, case_id, snapshot, conversation)
    receipt = send(new_case, "重新审查", key="different-conversation")
    assert run(new_case, receipt, ScriptProvider()).status == "completed"
    assert not DynamicRepository(engine).verified(receipt["run_id"])[1].get("needs_review")


@pytest.mark.parametrize("dynamic_case", ["苹果 数量12箱或8箱"], indirect=True)
@pytest.mark.parametrize("change", ["binding", "digest"])
def test_manual_selection_binding_boundary_and_digest_fence(dynamic_case, monkeypatch, change):
    from backend.app.api.auth import Principal
    from backend.app.db.assistant_repository import AssistantRepository
    from backend.app.db.item_binding_repository import ItemBindingRepository

    original, confirmed = corrected(dynamic_case, monkeypatch, "dynamic-rules-v6")
    _, engine, case_id, snapshot, _ = dynamic_case
    if change == "binding":
        ItemBindingRepository(engine).put(case_id, snapshot, [], actor=Principal("local", "local").key,
            checklist_version_id=confirmed["confirmed_version"]["id"], expected_revision=3,
            expected_checklist_revision=confirmed["checklist_revision"], expected_binding_revision=1,
            confirmed=True, idempotency_key="empty-new-binding", reason="确认新范围")
    receipt = send(dynamic_case, "重新审查", key="select-history")
    claim = RunRepository(engine).claim(receipt["run_id"], receipt["attempt_id"])
    AssistantRepository(engine).register_formal(claim, "review")
    store = DynamicRepository(engine)
    previous, stamp = store.manual_previous(claim)
    if change == "binding":
        assert previous is None
        return
    assert previous and previous["needs_review"]
    with engine.begin() as connection:
        connection.execute(update(dynamic_runs).where(dynamic_runs.c.run_id == original["run_id"]).values(payload_digest="b"*64))
    with pytest.raises(RepositoryError, match="dynamic_source_changed"):
        store.validate_manual_previous(claim, stamp)


@pytest.mark.parametrize("dynamic_case", ["苹果 数量12箱或8箱"], indirect=True)
def test_inherited_manual_reports_can_be_reviewed_again(dynamic_case, monkeypatch):
    corrected(dynamic_case, monkeypatch, "dynamic-rules-v5")
    for key in ("first-inheritance", "second-inheritance"):
        receipt = send(dynamic_case, "完整重审", key=key)
        assert run(dynamic_case, receipt, ScriptProvider()).status == "completed"
        payload = DynamicRepository(dynamic_case[1]).verified(receipt["run_id"])[1]
        assert payload["needs_review"]
        assert {f["value"] for f in payload["facts"] if f["supplier"] == "vendor-a"} == {"12", "8"}
