"""Unsaved buyer previews continue in one conversation without becoming authority."""

from copy import deepcopy

import pytest
from sqlalchemy import select, update

from backend.app.db.models import assistant_runs
from backend.app.db.run_executor import execute_run
from backend.app.db.run_repository import RepositoryError
from backend.app.evidence.repository import digest
from backend.tests import test_document_ingestion
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_assistant_context import phone_requirements

ingestion_case = test_document_ingestion.ingestion_case


def submit(client, conversation, text, key):
    response = client.post(f"/api/v1/conversations/{conversation}/messages",
        headers={"Idempotency-Key": key}, json={"content": text, "operation": "assistant", "expected_revision": 0})
    assert response.status_code == 202, response.text
    return response.json()


def execute(client, engine, receipt, tool, args):
    report = execute_run(engine, receipt["run_id"], receipt["attempt_id"],
        Decisions((tool, args)), heartbeat_interval_seconds=60)
    assert report.status == "completed", report
    response = client.get(f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result")
    assert response.status_code == 200, response.text
    return response.json()


def first_preview(fixture):
    client, engine, _, case = fixture
    conversation = client.post(f"/api/v1/cases/{case}/conversations").json()["id"]
    receipt = submit(client, conversation, "我要采购20台苹果手机", "quantity")
    result = execute(client, engine, receipt, "propose_changes", {
        "items": [{"item_id": "phone", "name": "苹果手机"}], "operations": [{
            "op": "add", "reason": "买方明确数量", "criterion": {"item_id": "phone", "name": "采购数量",
            "rule": {"type": "numeric", "operator": "eq", "value": "20", "unit": "台"}}}]})
    return conversation, receipt, result


def seven_patch():
    return {"items": [], "operations": [{"op": "add", "reason": "买方要求加入待确认方向",
        "criterion": {k: v for k, v in criterion.items() if k not in {"criterion_id", "source"}}}
        for criterion in phone_requirements().model_dump(mode="json")["criteria"]]}


def test_ten_unsaved_verbose_requirements_survive_followup_append_and_explicit_confirmation(ingestion_case):
    from backend.tests.test_assistant_stages import addition, definition

    client, engine, _, case = ingestion_case
    conversation = client.post(f"/api/v1/cases/{case}/conversations").json()["id"]
    initial = definition(10, verbose=True).model_dump(mode="json")
    receipt = submit(client, conversation, "准备十项手机采购要求", "ten-initial")
    first = execute(client, engine, receipt, "propose_changes", {"items": initial["items"], "operations": [
        {"op": "add", "reason": "买方明确加入", "criterion": {k: v for k, v in c.items() if k not in {"criterion_id", "source"}}}
        for c in initial["criteria"]]})
    original = first["result"]["candidate"]
    receipt = submit(client, conversation, "再给审核指标加上一个交期吧", "ten-deadline")
    latest = execute(client, engine, receipt, "propose_changes", addition())
    candidate = latest["result"]["candidate"]
    assert len(candidate["criteria"]) == 11
    assert candidate["criteria"][:-1] == original["criteria"]
    checklist = f"/api/v1/cases/{case}/snapshots/{latest['snapshot_id']}/checklist"
    assert client.get(checklist).json()["state"] == "empty"
    saved = client.put(checklist, headers={"Idempotency-Key": "save-eleven"}, json={**candidate,
        "expected_revision": 0, "expected_checklist_revision": 0, "reason": "明确保存十一项"})
    assert saved.status_code == 200, saved.text
    confirmed = client.post(checklist + "/confirm", headers={"Idempotency-Key": "confirm-eleven"}, json={
        "expected_revision": 0, "expected_checklist_revision": saved.json()["checklist_revision"],
        "criterion_ids": [c["criterion_id"] for c in candidate["criteria"]], "confirmed": True, "reason": "明确确认"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed_version"]["definition"] == candidate


def test_unsaved_quantity_survives_chat_and_seven_additions_then_explicit_save_confirm(ingestion_case):
    client, engine, _, case = ingestion_case
    conversation, first, initial = first_preview(ingestion_case)
    assert initial["is_current"] is True and initial["checklist_revision"] == 0
    checklist = f"/api/v1/cases/{case}/snapshots/{initial['snapshot_id']}/checklist"
    assert client.get(checklist).json()["state"] == "empty"
    chat = submit(client, conversation, "还有吗", "chat")
    execute(client, engine, chat, "chat", {"answer": "还可明确规格、交付等要求。"})
    receipt = submit(client, conversation, "把上述七个待确认方向也加进来", "seven")
    result = execute(client, engine, receipt, "propose_changes", seven_patch())
    definition = result["result"]["candidate"]
    assert len(definition["criteria"]) == 8
    assert definition["criteria"][0] == initial["result"]["candidate"]["criteria"][0]
    assert len(result["result"]["changes"]) == 7
    assert result["is_current"] is True and result["checklist_revision"] == 0
    assert client.get(checklist).json()["state"] == "empty"
    prefix = f"/api/v1/agent-runs/{receipt['run_id']}"
    assert client.get(prefix + "/checks").json()["checks"] == []
    assert client.get(prefix + "/export").status_code == 409
    chat = submit(client, conversation, "所以接下来要补充供应商报价材料吗", "chat-eight")
    execute(client, engine, chat, "chat", {"answer": "是的，先补充供应商报价或承诺资料。"})
    saved = client.put(checklist, headers={"Idempotency-Key": "save-eight"}, json={**definition,
        "expected_revision": 0, "expected_checklist_revision": 0, "reason": "明确保存完整八项"})
    assert saved.status_code == 200, saved.text
    confirmed = client.post(checklist + "/confirm", headers={"Idempotency-Key": "confirm-eight"}, json={
        "expected_revision": 0, "expected_checklist_revision": saved.json()["checklist_revision"],
        "criterion_ids": [c["criterion_id"] for c in definition["criteria"]], "confirmed": True, "reason": "明确确认"})
    assert confirmed.status_code == 200, confirmed.text
    assert len(confirmed.json()["confirmed_version"]["definition"]["criteria"]) == 8
    old = client.get(f"/api/v1/agent-runs/{first['run_id']}/assistant-result")
    assert old.status_code == 200 and old.json()["is_current"] is False
    assert client.get(prefix + "/assistant-result").json()["is_current"] is False


def test_preview_update_and_explicit_remove_use_original_ids(ingestion_case):
    client, engine, _, _ = ingestion_case
    conversation, _, initial = first_preview(ingestion_case)
    criterion = initial["result"]["candidate"]["criteria"][0]
    edit = {k: v for k, v in criterion.items() if k not in {"criterion_id", "source"}}
    edit["rule"]["value"] = "30"
    receipt = submit(client, conversation, "数量改成30台", "update")
    updated = execute(client, engine, receipt, "propose_changes", {"items": [], "operations": [{
        "op": "update", "criterion_id": criterion["criterion_id"], "criterion": edit, "reason": "买方调整"}]})
    assert updated["result"]["candidate"]["criteria"][0]["criterion_id"] == criterion["criterion_id"]
    assert updated["result"]["candidate"]["criteria"][0]["rule"]["value"] == "30"
    receipt = submit(client, conversation, "先加上规格待确认", "manual")
    execute(client, engine, receipt, "propose_changes", {"items": [], "operations": seven_patch()["operations"][:1]})
    receipt = submit(client, conversation, "删除采购数量指标", "remove")
    removed = execute(client, engine, receipt, "propose_changes", {"items": [], "operations": [{
        "op": "remove", "criterion_id": criterion["criterion_id"], "reason": "买方明确删除"}]})
    assert [c["name"] for c in removed["result"]["candidate"]["criteria"]] == ["机型/规格"]


def test_other_conversation_does_not_inherit_preview(ingestion_case):
    client, engine, _, case = ingestion_case
    first_preview(ingestion_case)
    other = client.post(f"/api/v1/cases/{case}/conversations").json()["id"]
    receipt = submit(client, other, "我采购服装", "other")
    result = execute(client, engine, receipt, "propose_changes", {"items": [{"item_id": "shirt", "name": "服装"}],
        "operations": [{"op": "add", "reason": "用户要求", "criterion": {"item_id": "shirt", "name": "尺码",
        "rule": {"type": "manual", "instruction": "待明确尺码"}}}]})
    assert [i["item_id"] for i in result["result"]["candidate"]["items"]] == ["shirt"]


def test_bound_preview_is_rechecked_before_execution_and_historical_read(ingestion_case):
    client, engine, _, _ = ingestion_case
    conversation, first, initial = first_preview(ingestion_case)
    second = submit(client, conversation, "补充规格方向", "second")
    with engine.begin() as connection:
        payload = deepcopy(initial["result"])
        payload["candidate"]["criteria"][0]["rule"]["value"] = "999"
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == first["run_id"])
            .values(payload_json=payload, payload_digest=digest(payload)))
    provider = Decisions(("propose_changes", seven_patch()))
    with pytest.raises(RepositoryError, match="assistant_source_changed"):
        execute_run(engine, second["run_id"], second["attempt_id"], provider, heartbeat_interval_seconds=60)
    assert provider.messages == []
    with engine.connect() as connection:
        assert connection.scalar(select(assistant_runs.c.payload_json).where(assistant_runs.c.run_id == second["run_id"])) is None


def test_saved_head_replaces_pending_preview_and_old_candidate_remains_readable(ingestion_case):
    client, engine, _, case = ingestion_case
    conversation, _, initial = first_preview(ingestion_case)
    changed = deepcopy(initial["result"]["candidate"])
    changed["criteria"][0]["rule"]["value"] = "50"
    checklist = f"/api/v1/cases/{case}/snapshots/{initial['snapshot_id']}/checklist"
    response = client.put(checklist, headers={"Idempotency-Key": "manual-save"}, json={**changed,
        "expected_revision": 0, "expected_checklist_revision": 0, "reason": "人工编辑后保存"})
    assert response.status_code == 200, response.text
    second = submit(client, conversation, "再加规格方向", "after-save")
    result = execute(client, engine, second, "propose_changes", {"items": [], "operations": seven_patch()["operations"][:1]})
    assert result["result"]["candidate"]["criteria"][0]["rule"]["value"] == "50"
    assert result["checklist_revision"] == 1


def test_uploaded_new_snapshot_does_not_inherit_empty_snapshot_preview(ingestion_case):
    from backend.app.db.ingestion import DocumentIngestion

    client, engine, storage, case = ingestion_case
    conversation, first, _ = first_preview(ingestion_case)
    upload = test_document_ingestion._upload(client, case, "vendor-a-quote.xlsx", "quote", 0, "new-quote", "vendor-a")
    assert DocumentIngestion(engine, storage).process_document(case, upload["snapshot_id"], upload["document_id"])["parse_status"] == "complete"
    response = client.post(f"/api/v1/conversations/{conversation}/messages", headers={"Idempotency-Key": "after-upload"},
        json={"content": "重新梳理苹果手机要求", "operation": "assistant", "expected_revision": 1})
    assert response.status_code == 202, response.text
    with engine.connect() as connection:
        assert connection.scalar(select(assistant_runs.c.base_json).where(assistant_runs.c.run_id == response.json()["run_id"])) is None
    result = execute(client, engine, response.json(), "propose_changes", {
        "items": [{"item_id": "phone-new", "name": "苹果手机"}], "operations": [{"op": "add", "reason": "重新梳理",
        "criterion": {"item_id": "phone-new", "name": "型号", "rule": {"type": "manual", "instruction": "待明确型号"}}}]})
    assert len(result["result"]["candidate"]["criteria"]) == 1
    assert client.get(f"/api/v1/agent-runs/{first['run_id']}/assistant-result").json()["is_current"] is False


def test_retry_after_restart_keeps_frozen_preview(ingestion_case):
    from sqlalchemy import create_engine

    client, engine, _, _ = ingestion_case
    conversation, _, initial = first_preview(ingestion_case)
    receipt = submit(client, conversation, "再加规格方向", "restart")
    report = execute_run(engine, receipt["run_id"], receipt["attempt_id"], Decisions(("unknown", {})), heartbeat_interval_seconds=60)
    assert report.status == "partial"
    retry = client.post(f"/api/v1/agent-runs/{receipt['run_id']}/retry", headers={"Idempotency-Key": "retry"},
        json={"expected_revision": 0})
    assert retry.status_code == 202, retry.text
    reopened = create_engine(engine.url)
    try:
        result = execute(client, reopened, retry.json(), "propose_changes", {"items": [], "operations": seven_patch()["operations"][:1]})
    finally:
        reopened.dispose()
    assert result["result"]["candidate"]["criteria"][0] == initial["result"]["candidate"]["criteria"][0]
    assert len(result["result"]["candidate"]["criteria"]) == 2


def test_unsaved_preview_cannot_authorize_formal_review(ingestion_case):
    client, engine, _, _ = ingestion_case
    conversation, _, _ = first_preview(ingestion_case)
    receipt = submit(client, conversation, "正式审查", "formal")
    report = execute_run(engine, receipt["run_id"], receipt["attempt_id"],
        Decisions(("review_checklist", {}), ("review_checklist", {})), heartbeat_interval_seconds=60)
    assert report.status == "waiting_user" and report.error_code == "checklist_confirmation_required"
    prefix = f"/api/v1/agent-runs/{receipt['run_id']}"
    assert client.get(prefix + "/checks").json()["checks"] == []
    assert client.get(prefix + "/export").status_code == 409


def test_preview_depth_limit_requires_save_and_does_not_drop_prior_requirements(ingestion_case, monkeypatch):
    import backend.app.db.assistant_preview as preview

    monkeypatch.setattr(preview, "MAX_PREVIEW_DEPTH", 2)
    client, engine, _, case = ingestion_case
    conversation, _, initial = first_preview(ingestion_case)
    second = submit(client, conversation, "再加规格方向", "second")
    result = execute(client, engine, second, "propose_changes", {"items": [], "operations": seven_patch()["operations"][:1]})
    denied = client.post(f"/api/v1/conversations/{conversation}/messages", headers={"Idempotency-Key": "limit"},
        json={"content": "还有吗", "operation": "assistant", "expected_revision": 0})
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "assistant_preview_limit"
    checklist = f"/api/v1/cases/{case}/snapshots/{initial['snapshot_id']}/checklist"
    saved = client.put(checklist, headers={"Idempotency-Key": "save-at-limit"}, json={**result["result"]["candidate"],
        "expected_revision": 0, "expected_checklist_revision": 0, "reason": "保存后继续"})
    assert saved.status_code == 200, saved.text
    receipt = submit(client, conversation, "继续补充", "after-limit-save")
    continued = execute(client, engine, receipt, "propose_changes", {"items": [], "operations": seven_patch()["operations"][1:2]})
    assert len(continued["result"]["candidate"]["criteria"]) == 3


@pytest.mark.parametrize("field", ["instruction", "patch", "candidate", "changes", "source_digest"])
def test_completed_child_rejects_parent_tampering_even_with_recomputed_digest(ingestion_case, field):
    client, engine, _, _ = ingestion_case
    conversation, first, initial = first_preview(ingestion_case)
    second = submit(client, conversation, "再加规格方向", "second")
    execute(client, engine, second, "propose_changes", {"items": [], "operations": seven_patch()["operations"][:1]})
    with engine.begin() as connection:
        payload = deepcopy(initial["result"])
        if field == "instruction":
            payload[field] = "采购999台"
        elif field == "patch":
            payload[field]["operations"][0]["criterion"]["rule"]["value"] = "999"
        elif field == "candidate":
            payload[field]["criteria"][0]["rule"]["value"] = "999"
        elif field == "changes":
            payload[field] = []
        else:
            payload[field] = "0" * 64
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == first["run_id"])
            .values(payload_json=payload, payload_digest=digest(payload)))
    response = client.get(f"/api/v1/agent-runs/{second['run_id']}/assistant-result")
    assert response.status_code == 409 and response.json()["error"]["code"] == "assistant_source_changed"


@pytest.mark.parametrize("field", ["instruction", "candidate", "changes", "source_digest"])
def test_latest_parent_is_replayed_against_user_message_and_actual_sources(ingestion_case, field):
    client, engine, _, _ = ingestion_case
    conversation, first, initial = first_preview(ingestion_case)
    with engine.begin() as connection:
        payload = deepcopy(initial["result"])
        if field == "instruction":
            payload[field] = "采购999台"
        elif field == "candidate":
            payload[field]["criteria"][0]["rule"]["value"] = "999"
        elif field == "changes":
            payload[field] = []
        else:
            payload[field] = "0" * 64
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == first["run_id"])
            .values(payload_json=payload, payload_digest=digest(payload)))
    response = client.post(f"/api/v1/conversations/{conversation}/messages", headers={"Idempotency-Key": "after-corruption"},
        json={"content": "再加规格方向", "operation": "assistant", "expected_revision": 0})
    if field != "source_digest":
        assert response.status_code == 409 and response.json()["error"]["code"] == "assistant_source_changed"
    else:
        assert response.status_code == 202, response.text
        provider = Decisions(("propose_changes", seven_patch()))
        with pytest.raises(RepositoryError, match="assistant_source_changed"):
            execute_run(engine, response.json()["run_id"], response.json()["attempt_id"], provider, heartbeat_interval_seconds=60)
        assert provider.messages == []
