"""Assistant uses the durable Run envelope, including an explicit empty snapshot."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select, update

from backend.app.db.models import input_snapshots, task_memory
from backend.app.db.run_executor import execute_run
from backend.tests import test_document_ingestion, test_dynamic_workflow
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_dynamic_agent import ScriptProvider

ingestion_case = test_document_ingestion.ingestion_case
dynamic_case = test_dynamic_workflow.dynamic_case


def send(case, content, key="assistant"):
    client, _, _, _, conv = case
    response = client.post(f"/api/v1/conversations/{conv}/messages", headers={"Idempotency-Key": key},
        json={"content": content, "operation": "assistant", "expected_revision": 3})
    assert response.status_code == 202, response.text
    return response.json()


def run(case, receipt, provider):
    return execute_run(case[1], receipt["run_id"], receipt["attempt_id"], provider, heartbeat_interval_seconds=60)


def test_empty_case_chat_api_history_and_nonformal_exports(ingestion_case):
    client, engine, _, case = ingestion_case
    conv = client.post(f"/api/v1/cases/{case}/conversations").json()["id"]
    body = {"content": "你好", "operation": "assistant", "expected_revision": 0}
    response = client.post(f"/api/v1/conversations/{conv}/messages", headers={"Idempotency-Key": "hello"}, json=body)
    assert response.status_code == 202, response.text
    run = response.json()
    result = execute_run(engine, run["run_id"], run["attempt_id"], Decisions(("chat", {"answer": "你好，请说明本次需求。"})), heartbeat_interval_seconds=60)
    assert result.status == "completed"
    found = client.get(f"/api/v1/agent-runs/{run['run_id']}/assistant-result")
    assert found.status_code == 200, found.text
    assert found.json()["result"]["kind"] == "chat"
    assert client.get(f"/api/v1/agent-runs/{run['run_id']}/checks").json()["checks"] == []
    assert client.get(f"/api/v1/agent-runs/{run['run_id']}/facts").json()["fact_sets"] == []
    assert client.get(f"/api/v1/agent-runs/{run['run_id']}/export").status_code == 409
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(input_snapshots)) == 1
        state = connection.scalar(select(task_memory.c.state_json))
    assert state["checkpoint"]["version"] == 5
    replay = client.post(f"/api/v1/conversations/{conv}/messages", headers={"Idempotency-Key": "hello"}, json=body)
    assert replay.status_code == 202 and replay.json() == run
    # Existing review behavior keeps its revision >= 1 contract.
    legacy = client.post(f"/api/v1/conversations/{conv}/messages", headers={"Idempotency-Key": "old"}, json={"content": "审查", "expected_revision": 0})
    assert legacy.status_code == 422


def test_seven_confirmed_manual_requirements_chat_persists_context_without_formal_report(ingestion_case):
    from backend.app.db.models import agent_runs
    from backend.tests.test_assistant_context import phone_requirements

    client, engine, _, case = ingestion_case
    conv = client.post(f"/api/v1/cases/{case}/conversations").json()["id"]
    messages_url = f"/api/v1/conversations/{conv}/messages"
    initial = client.post(messages_url, headers={"Idempotency-Key": "setup"},
        json={"content": "准备采购手机", "operation": "assistant", "expected_revision": 0}).json()
    execute_run(engine, initial["run_id"], initial["attempt_id"], Decisions(("chat", {"answer": "请先设置采购要求。"})), heartbeat_interval_seconds=60)
    snapshot = client.get(f"/api/v1/agent-runs/{initial['run_id']}").json()["snapshot_id"]
    checklist_url = f"/api/v1/cases/{case}/snapshots/{snapshot}/checklist"
    definition = phone_requirements().model_dump(mode="json")
    saved = client.put(checklist_url, headers={"Idempotency-Key": "seven-save"},
        json={**definition, "expected_revision": 0, "expected_checklist_revision": 0, "reason": "明确保存"})
    assert saved.status_code == 200, saved.text
    confirmed = client.post(checklist_url + "/confirm", headers={"Idempotency-Key": "seven-confirm"}, json={
        "expected_revision": 0, "expected_checklist_revision": saved.json()["checklist_revision"],
        "criterion_ids": [c["criterion_id"] for c in definition["criteria"]], "confirmed": True, "reason": "明确确认"})
    assert confirmed.status_code == 200, confirmed.text
    request = client.post(messages_url, headers={"Idempotency-Key": "after-seven"}, json={
        "content": "所以我现在需要补充采购文件、供应商报价、承诺材料之类的", "operation": "assistant", "expected_revision": 0}).json()
    report = execute_run(engine, request["run_id"], request["attempt_id"],
        Decisions(("chat", {"answer": "是的，请补充供应商报价或承诺资料；具体型号等要求也仍需明确。"})), heartbeat_interval_seconds=60)
    assert report.status == "completed" and report.model_calls == 1
    prefix = f"/api/v1/agent-runs/{request['run_id']}"
    result = client.get(prefix + "/assistant-result")
    assert result.status_code == 200 and result.json()["result"]["kind"] == "chat"
    assert client.get(prefix + "/checks").json()["checks"] == []
    assert client.get(prefix + "/export").status_code == 409
    assert client.get(checklist_url).json() == {k: v for k, v in confirmed.json().items() if k != "excluded_criterion_ids"}
    with engine.connect() as connection:
        manifest = connection.scalar(select(agent_runs.c.context_manifest_json).where(agent_runs.c.id == request["run_id"]))
    audit = manifest["assistant_context"]
    assert audit["mandatory_overflow"] is False and audit["input_bound"] <= audit["prompt_limit"]
    assert all(type(value) in {int, bool} for value in audit.values())


def test_empty_chat_user_defined_candidate_explicit_save_confirm_then_review_waits(ingestion_case):
    client, engine, _, case = ingestion_case
    conv = client.post(f"/api/v1/cases/{case}/conversations").json()["id"]
    body = {"content": "采购苹果，数量至少20箱", "operation": "assistant", "expected_revision": 0}
    response = client.post(f"/api/v1/conversations/{conv}/messages", headers={"Idempotency-Key": "create-demand"}, json=body)
    assert response.status_code == 202, response.text
    run = response.json()
    patch = {"items": [{"item_id": "apple", "name": "苹果"}], "operations": [{
        "op": "add", "reason": "用户采购指令", "criterion": {"item_id": "apple", "name": "数量",
        "rule": {"type": "numeric", "operator": "gte", "value": "20", "unit": "箱"}}}]}
    report = execute_run(engine, run["run_id"], run["attempt_id"], Decisions(("propose_changes", patch)), heartbeat_interval_seconds=60)
    assert report.status == "completed", report
    preview = client.get(f"/api/v1/agent-runs/{run['run_id']}/proposal").json()
    url = f"/api/v1/cases/{case}/snapshots/{client.get('/api/v1/agent-runs/' + run['run_id']).json()['snapshot_id']}/checklist"
    saved = client.put(url, headers={"Idempotency-Key": "save-empty"}, json={**preview["definition"],
        "expected_revision": 0, "expected_checklist_revision": 0, "reason": "买方设置"})
    assert saved.status_code == 200, saved.text
    confirmed = client.post(url + "/confirm", headers={"Idempotency-Key": "confirm-empty"}, json={
        "expected_revision": 0, "expected_checklist_revision": saved.json()["checklist_revision"],
        "criterion_ids": [preview["definition"]["criteria"][0]["criterion_id"]], "confirmed": True, "reason": "买方确认"})
    assert confirmed.status_code == 200, confirmed.text
    request = client.post(f"/api/v1/conversations/{conv}/messages", headers={"Idempotency-Key": "review-empty"},
        json={"content": "正式审查", "operation": "assistant", "expected_revision": 0}).json()
    report = execute_run(engine, request["run_id"], request["attempt_id"], Decisions(("review_checklist", {}), ("review_checklist", {})), heartbeat_interval_seconds=60)
    assert report.status == "waiting_user", report
    result = client.get(f"/api/v1/agent-runs/{request['run_id']}/assistant-result")
    assert result.status_code == 200, result.text
    assert "供应商" in report.answer_text
    assert client.get(f"/api/v1/agent-runs/{request['run_id']}/checks").json()["checks"] == []


def test_assistant_formal_review_preserves_dynamic_validation_and_shared_usage(dynamic_case):
    from backend.app.db.models import assistant_runs, dynamic_runs

    confirmed, _ = test_dynamic_workflow.confirm(dynamic_case)
    receipt = send(dynamic_case, "按确认指标正式审查")
    report = run(dynamic_case, receipt, ScriptProvider())
    assert report.status == "completed", report
    assert report.model_calls == 4  # routing + dynamic decision + two supplier batches
    url = f"/api/v1/agent-runs/{receipt['run_id']}"
    result = dynamic_case[0].get(url + "/assistant-result")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["kind"] == "review"
    checks = dynamic_case[0].get(url + "/checks").json()
    assert len(checks["checks"]) == 2 and all(c["is_formal"] for c in checks["checks"])
    assert checks["checklist_version_id"] == confirmed["confirmed_version"]["id"]
    assert dynamic_case[0].get(url + "/export").status_code == 200
    with dynamic_case[1].connect() as connection:
        assert connection.scalar(select(assistant_runs.c.run_id)) == receipt["run_id"]
        assert connection.scalar(select(dynamic_runs.c.run_id).where(dynamic_runs.c.run_id == receipt["run_id"])) == receipt["run_id"]


@pytest.mark.parametrize("dynamic_case", ["苹果 数量12箱或8箱"], indirect=True)
@pytest.mark.parametrize("tamper", [False, True])
def test_assistant_fact_correction_keeps_chat_report_and_checkpoint_consistent(dynamic_case, tamper):
    from backend.app.criteria.facts import FactCandidate
    from backend.app.db.models import agent_runs, assistant_runs

    test_dynamic_workflow.confirm(dynamic_case)
    client, engine, _, _, _ = dynamic_case
    receipt = send(dynamic_case, "审查苹果的数量")
    assert run(dynamic_case, receipt, ScriptProvider()).status == "completed"
    run_id = receipt["run_id"]
    prefix = f"/api/v1/agent-runs/{run_id}"
    before = client.get(prefix + "/assistant-result")
    assert before.status_code == 200, before.text
    facts = client.get(prefix + "/facts").json()
    group = next(g for g in facts["fact_sets"] if g["supplier"] == "vendor-a")
    old = group["facts"][0]
    candidate = {k: old[k] for k in FactCandidate.model_fields}
    candidate.update(value="8", unit="箱")
    if tamper:
        with engine.begin() as connection:
            payload = deepcopy(before.json()["result"])
            payload["answer"] = "伪造答案"
            connection.execute(update(assistant_runs).where(
                assistant_runs.c.run_id == run_id).values(payload_json=payload))
    response = client.post(prefix + "/fact-corrections",
        headers={"Idempotency-Key": "assistant-correction"}, json={
            "fact_id": old["fact_id"], "candidate": candidate, "reason": "同源备选",
            "expected_revision": 3, "expected_payload_digest": facts["payload_digest"],
        })
    if tamper:
        assert response.status_code == 409, response.text
        assert client.get(prefix + "/facts").json()["payload_digest"] == facts["payload_digest"]
        assert not client.get(prefix + "/fact-corrections").json()["corrections"]
        return
    assert response.status_code == 201, response.text
    result = client.get(prefix + "/assistant-result")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["dynamic_payload_digest"] == response.json()["payload_digest"]
    with engine.connect() as connection:
        stored = connection.execute(select(agent_runs).where(agent_runs.c.id == run_id)).mappings().one()
    assert result.json()["result"]["answer"] == stored["answer_text"]
    assert "本次存在人工修正，结论仍需人工复核" in stored["answer_text"]
    assert "指标审查完成" in stored["answer_text"]
    assert "未覆盖项" not in stored["answer_text"]
    assert "部分完成" not in stored["answer_text"]
    assert stored["memory_json"]["version"] == 5
    assert stored["memory_json"]["payload_digest"] == result.json()["payload_digest"]
    checkpoints = client.get(prefix + "/checks").json()["checks"]
    assert not any(c["is_formal"] for c in checkpoints)


def test_fixed_base_candidate_historical_get_and_diff_tamper_rejected(dynamic_case):
    from backend.app.db.models import assistant_runs
    from backend.app.evidence.repository import digest

    confirmed, original = test_dynamic_workflow.confirm(dynamic_case)
    client, engine, case, snapshot, _ = dynamic_case
    target = {k: v for k, v in original["criteria"][0].items() if k not in {"source", "criterion_id"}}
    target["rule"]["value"] = "20"
    receipt = send(dynamic_case, "把苹果数量改成20箱")
    report = run(dynamic_case, receipt, Decisions(("propose_changes", {"items": [], "operations": [
        {"op": "update", "criterion_id": original["criteria"][0]["criterion_id"], "criterion": target, "reason": "买方调整"}]})))
    assert report.status == "completed", report
    url = f"/api/v1/agent-runs/{receipt['run_id']}"
    preview = client.get(url + "/proposal").json()
    checklist = f"/api/v1/cases/{case}/snapshots/{snapshot}/checklist"
    assert client.get(checklist).json() == {k: v for k, v in confirmed.items() if k != "excluded_criterion_ids"}
    source = preview["definition"]["criteria"][0]["source"]
    assert source["kind"] == "user_defined" and source["context"] == original["criteria"][0]["source"]["context"]
    assert client.get(url + "/checks").json()["checks"] == []
    client.put(checklist, headers={"Idempotency-Key": "save-changes"}, json={**preview["definition"],
        "expected_revision": 3, "expected_checklist_revision": confirmed["checklist_revision"], "reason": "明确保存"})
    historical = client.get(url + "/proposal")
    assert historical.status_code == 200 and historical.json()["is_current"] is False
    assert historical.json()["definition"] == preview["definition"]
    with engine.begin() as connection:
        payload = deepcopy(connection.scalar(select(assistant_runs.c.payload_json).where(assistant_runs.c.run_id == receipt["run_id"])))
        payload["changes"][0]["after"]["rule"]["value"] = "999"
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt["run_id"]).values(payload_json=payload, payload_digest=digest(payload)))
    assert client.get(url + "/assistant-result").status_code == 409


def test_new_chat_run_cannot_promote_previous_partial_dynamic_findings(dynamic_case):
    from backend.app.providers.base import ProviderError

    test_dynamic_workflow.confirm(dynamic_case)

    class FailLast(ScriptProvider):
        def extract(self, messages, schema, **options):
            if self.extractions:
                raise ProviderError("rate_limit")
            return super().extract(messages, schema, **options)

    receipt = send(dynamic_case, "正式审查")
    assert run(dynamic_case, receipt, FailLast()).status == "partial"
    url = f"/api/v1/agent-runs/{receipt['run_id']}"
    chat = send(dynamic_case, "先聊一下下一步", key="new-chat")
    assert chat["run_id"] != receipt["run_id"]
    report = run(dynamic_case, chat, Decisions(("chat", {"answer": "可以先说明下一步需求。"})))
    assert report.status == "completed"
    assert dynamic_case[0].get(url).json()["status"] == "partial"
    url = f"/api/v1/agent-runs/{chat['run_id']}"
    assert dynamic_case[0].get(url + "/checks").json()["checks"] == []
    assert dynamic_case[0].get(url + "/facts").json()["fact_sets"] == []
    assert dynamic_case[0].get(url + "/export").status_code == 409


def test_empty_retry_keeps_one_snapshot_and_real_same_conversation_history(ingestion_case):
    client, engine, _, case = ingestion_case
    conv = client.post(f"/api/v1/cases/{case}/conversations").json()["id"]
    url = f"/api/v1/conversations/{conv}/messages"
    body = {"content": "你好", "operation": "assistant", "expected_revision": 0}
    receipt = client.post(url, headers={"Idempotency-Key": "invalid-first"}, json=body).json()
    report = execute_run(engine, receipt["run_id"], receipt["attempt_id"], Decisions(("execute", {"actor": "admin"})), heartbeat_interval_seconds=60)
    assert report.status == "partial"
    retry = client.post(f"/api/v1/agent-runs/{receipt['run_id']}/retry", headers={"Idempotency-Key": "retry-empty"}, json={"expected_revision": 0})
    assert retry.status_code == 202, retry.text
    report = execute_run(engine, receipt["run_id"], retry.json()["attempt_id"], Decisions(("chat", {"answer": "你好"})), heartbeat_interval_seconds=60)
    assert report.status == "completed"
    receipt2 = client.post(url, headers={"Idempotency-Key": "second-message"}, json=body).json()
    provider = Decisions(("chat", {"answer": "已收到本会话前一轮内容。"}))
    execute_run(engine, receipt2["run_id"], receipt2["attempt_id"], provider, heartbeat_interval_seconds=60)
    assert any(m.type == "ai" and m.content == "你好" for m in provider.messages[0])
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(input_snapshots)) == 1


def test_get_rejects_citation_to_original_not_actually_read_by_this_run(dynamic_case):
    from backend.app.agent.assistant import checked_citation, render_evidence
    from backend.app.db.agent_snapshot import load_agent_snapshot
    from backend.app.db.models import agent_runs, assistant_runs
    from backend.app.evidence.repository import digest

    client, engine, case, snapshot, _ = dynamic_case
    repo = load_agent_snapshot(engine, case, snapshot)
    block = next(b for b in repo.blocks if b.supplier == "vendor-a")
    other = next(b for b in repo.blocks if b.supplier == "vendor-b")
    receipt = send(dynamic_case, "甲供应商苹果数量")
    report = run(dynamic_case, receipt, Decisions(("search_evidence", {"query": "苹果数量", "supplier": "vendor-a"}),
        ("answer_evidence", {"answer": block.text, "citations": [{"evidence_id": block.evidence_id, "quote": block.text}]})))
    assert report.status == "completed", report
    with engine.begin() as connection:
        payload = deepcopy(connection.scalar(select(assistant_runs.c.payload_json).where(assistant_runs.c.run_id == receipt["run_id"])))
        payload["citations"] = [checked_citation(repo, other.evidence_id, other.text)]
        payload["answer"] = render_evidence(payload["citations"], payload["retrieval"])
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt["run_id"]).values(payload_json=payload, payload_digest=digest(payload)))
        connection.execute(update(agent_runs).where(agent_runs.c.id == receipt["run_id"]).values(answer_text=payload["answer"]))
    assert client.get(f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result").status_code == 409


def test_assistant_result_unknown_run_is_not_found(ingestion_case):
    assert ingestion_case[0].get("/api/v1/agent-runs/not-a-run/assistant-result").status_code == 404


@pytest.mark.parametrize('dynamic_case', ['背景说明。' * 200 + '交付期限为签约后17个工作日',
    '旧样例：交付期限为签约后17个工作日。' + '背景说明。' * 200 + '最新承诺：交付期限为签约后17个工作日'], indirect=True)
def test_long_source_tail_survives_persistence_and_rejects_offset_tampering(dynamic_case):
    from backend.app.db.agent_snapshot import load_agent_snapshot
    from backend.app.db.models import assistant_runs
    from backend.app.evidence.repository import digest

    client, engine, case, snapshot, _ = dynamic_case
    repo = load_agent_snapshot(engine, case, snapshot)
    block = next(b for b in repo.blocks if b.supplier == 'vendor-a')
    receipt = send(dynamic_case, '甲供应商交付期限')
    report = run(dynamic_case, receipt, Decisions(('search_evidence', {'query': '交付期限', 'supplier': 'vendor-a', 'limit': 1}),
        ('answer_evidence', {"answer": '交付期限为签约后17个工作日', 'citations': [{'evidence_id': block.evidence_id, 'quote': '交付期限为签约后17个工作日'}]})))
    assert report.status == 'completed', report
    url = f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result"
    result = client.get(url)
    assert result.status_code == 200, result.text
    assert result.json()['result']['source_windows'][0]['start_char'] > 0
    context = ('最新承诺：' if '最新承诺：' in block.text else '') + '交付期限为签约后17个工作日'
    assert result.json()['result']['citations'][0]['start_char'] == block.text.rindex(context)
    assert result.json()['result']['citations'][0]['quote'] == context
    with engine.begin() as connection:
        payload = deepcopy(connection.scalar(select(assistant_runs.c.payload_json).where(assistant_runs.c.run_id == receipt['run_id'])))
        payload['citations'][0]['start_char'] = 0
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt['run_id']).values(payload_json=payload, payload_digest=digest(payload)))
    assert client.get(url).status_code == 409
    with engine.begin() as connection:
        # Restore the valid citation so the next assertion isolates window tampering.
        payload = deepcopy(result.json()['result'])
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt['run_id']).values(payload_json=payload, payload_digest=digest(payload)))
    with engine.begin() as connection:
        payload = deepcopy(connection.scalar(select(assistant_runs.c.payload_json).where(assistant_runs.c.run_id == receipt['run_id'])))
        payload['source_windows'][0]['start_char'] = 0
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt['run_id']).values(payload_json=payload, payload_digest=digest(payload)))
    assert client.get(url).status_code == 409


@pytest.mark.parametrize('dynamic_case', ['背景说明。' * 65 + '采购数量777台。'], indirect=True)
def test_hybrid_tail_windows_are_persisted_and_revalidated(dynamic_case):
    from backend.app.db.agent_snapshot import load_agent_snapshot
    from backend.tests.test_hybrid_retrieval import setup

    client, engine, case, snapshot, _ = dynamic_case
    repo = load_agent_snapshot(engine, case, snapshot)
    block = next(b for b in repo.blocks if b.supplier == 'vendor-a')
    receipt = send(dynamic_case, '甲供应商采购数量')

    def factory(engine, protected_repo, claim):
        retriever, _, _ = setup(protected_repo)
        retriever.close = lambda: None
        return retriever

    provider = Decisions(('search_evidence', {'query': '777', 'supplier': 'vendor-a', 'limit': 1}),
        ('answer_evidence', {"answer": '采购数量777台。', 'citations': [{'evidence_id': block.evidence_id, 'quote': '采购数量777台。'}]}))
    report = execute_run(engine, receipt['run_id'], receipt['attempt_id'], provider,
                         heartbeat_interval_seconds=60, retriever_factory=factory)
    assert report.status == 'completed', report
    response = client.get(f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result")
    assert response.status_code == 200, response.text
    payload = response.json()['result']
    assert len(payload['source_windows']) == 2
    assert payload['citations'][0]['start_char'] == block.text.index('采购数量777台。')
    assert payload['retrieval']['retrieval_mode'] == 'hybrid'


def test_legacy_prefix_evidence_payload_remains_readable(dynamic_case):
    from backend.app.db.agent_snapshot import load_agent_snapshot
    from backend.app.db.models import agent_runs, assistant_runs
    from backend.app.evidence.repository import digest

    client, engine, case, snapshot, _ = dynamic_case
    repo = load_agent_snapshot(engine, case, snapshot)
    block = next(b for b in repo.blocks if b.supplier == 'vendor-a')
    receipt = send(dynamic_case, '甲供应商苹果数量')
    report = run(dynamic_case, receipt, Decisions(('search_evidence', {'query': '苹果数量', 'supplier': 'vendor-a'}),
        ('answer_evidence', {"answer": block.text, 'citations': [{'evidence_id': block.evidence_id, 'quote': block.text}]})))
    assert report.status == 'completed', report
    with engine.begin() as connection:
        payload = deepcopy(connection.scalar(select(assistant_runs.c.payload_json).where(assistant_runs.c.run_id == receipt['run_id'])))
        payload.pop('source_windows')
        payload.pop('citation_offsets_version')
        payload.pop('citation_context_version')
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt['run_id']).values(payload_json=payload, payload_digest=digest(payload)))
        checkpoint = deepcopy(connection.scalar(select(agent_runs.c.memory_json).where(agent_runs.c.id == receipt['run_id'])))
        checkpoint.pop('citation_offsets_version')
        checkpoint.pop('citation_context_version')
        connection.execute(update(agent_runs).where(agent_runs.c.id == receipt['run_id']).values(memory_json=checkpoint))
    assert client.get(f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result").status_code == 200


@pytest.mark.parametrize('dynamic_case', ['旧样例：交付期限为签约后17个工作日。' + '背景说明。' * 200
    + '最新承诺：交付期限为签约后17个工作日'], indirect=True)
def test_new_citation_protocol_cannot_be_downgraded_by_removing_payload_marker(dynamic_case):
    from backend.app.db.agent_snapshot import load_agent_snapshot
    from backend.app.db.models import assistant_runs
    from backend.app.evidence.repository import digest

    client, engine, case, snapshot, _ = dynamic_case
    repo = load_agent_snapshot(engine, case, snapshot)
    block = next(b for b in repo.blocks if b.supplier == 'vendor-a')
    quote = '交付期限为签约后17个工作日'
    receipt = send(dynamic_case, '甲供应商交付期限')
    report = run(dynamic_case, receipt, Decisions(('search_evidence', {'query': '交付期限', 'supplier': 'vendor-a', 'limit': 1}),
        ('answer_evidence', {"answer": quote, 'citations': [{'evidence_id': block.evidence_id, 'quote': quote}]})))
    assert report.status == 'completed', report
    url = f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result"
    result = client.get(url)
    assert result.status_code == 200
    with engine.begin() as connection:
        payload = deepcopy(result.json()['result'])
        payload.pop('citation_offsets_version')
        payload['citations'][0]['start_char'] = block.text.index(quote)
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt['run_id']).values(payload_json=payload, payload_digest=digest(payload)))
    assert client.get(url).status_code == 409


@pytest.mark.parametrize('dynamic_case', ['空调单价不含税80元/台'], indirect=True)
@pytest.mark.parametrize('tamper', ['shorten', 'remove_marker'])
def test_complete_context_survives_persistence_and_cannot_be_weakened(dynamic_case, tamper):
    from backend.app.agent.assistant import render_evidence
    from backend.app.db.agent_snapshot import load_agent_snapshot
    from backend.app.db.models import agent_runs, assistant_runs
    from backend.app.evidence.repository import digest

    client, engine, case, snapshot, _ = dynamic_case
    repo = load_agent_snapshot(engine, case, snapshot)
    block = next(b for b in repo.blocks if b.supplier == 'vendor-a')
    receipt = send(dynamic_case, '甲供应商价格含税吗')
    report = run(dynamic_case, receipt, Decisions(
        ('search_evidence', {'query': '空调单价', 'supplier': 'vendor-a', 'limit': 1}),
        ('answer_evidence', {"answer": '含税80元/台', 'citations': [{'evidence_id': block.evidence_id, 'quote': '含税80元/台'}]})))
    assert report.status == 'completed', report
    url = f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result"
    response = client.get(url)
    assert response.status_code == 200
    payload = deepcopy(response.json()['result'])
    assert payload['citations'][0]['quote'] == block.text and payload['citation_context_version'] == 1
    if tamper == 'shorten':
        payload['citations'][0].update(quote='含税80元/台', start_char=block.text.index('含税80'))
        payload['answer'] = render_evidence(payload['citations'], payload['retrieval'])
    else:
        payload.pop('citation_context_version')
    with engine.begin() as connection:
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt['run_id']).values(
            payload_json=payload, payload_digest=digest(payload)))
        connection.execute(update(agent_runs).where(agent_runs.c.id == receipt['run_id']).values(answer_text=payload['answer']))
    assert client.get(url).status_code == 409


@pytest.mark.parametrize('same_conversation', [True, False])
def test_assistant_history_reader_enforces_conversation_scope_and_records_read(ingestion_case, same_conversation):
    from backend.app.db.models import agent_runs
    from backend.app.evidence.repository import digest

    client, engine, _, case = ingestion_case
    conv = client.post(f'/api/v1/cases/{case}/conversations').json()['id']

    def submit(conversation, content):
        return client.post(f'/api/v1/conversations/{conversation}/messages', headers={'Idempotency-Key': digest(content)},
            json={'content': content, 'operation': 'assistant', 'expected_revision': 0}).json()

    first = submit(conv, '黑色手机')
    report = execute_run(engine, first['run_id'], first['attempt_id'], Decisions(('chat', {'answer': '已记录你的描述。'})), heartbeat_interval_seconds=60)
    assert report.status == 'completed'
    with engine.connect() as connection:
        message_id = connection.scalar(select(agent_runs.c.message_id).where(agent_runs.c.id == first['run_id']))
    destination = conv if same_conversation else client.post(f'/api/v1/cases/{case}/conversations').json()['id']
    second = submit(destination, '我刚才说什么颜色')
    provider = Decisions(('read_conversation_history', {'message_ids': [message_id], 'part': 'question'}),
        ('chat', {'answer': '你先前说黑色。'}))
    report = execute_run(engine, second['run_id'], second['attempt_id'], provider, heartbeat_interval_seconds=60)
    with engine.connect() as connection:
        manifest = connection.scalar(select(agent_runs.c.context_manifest_json).where(agent_runs.c.id == second['run_id']))
    if same_conversation:
        assert report.status == 'completed', report
        assert '黑色手机' in provider.messages[1][-1].content
        assert manifest['history_reads'][0]['message_id'] == message_id
        assert manifest['assistant_context']['history_reads'] == 1
    else:
        assert report.status == 'partial' and report.error_code == 'history_scope'
        assert len(provider.messages) == 2 and not manifest.get('history_reads')
        assert '当前可回读' in report.answer_text
