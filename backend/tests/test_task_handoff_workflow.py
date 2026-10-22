"""SQLite API-to-worker handoff keeps historical text separate from current facts."""

import json

from sqlalchemy import select

from backend.app.db.models import agent_runs, conversation_summaries
from backend.app.db.run_executor import execute_run
from backend.app.providers.base import ProviderError
from backend.tests import test_document_ingestion, test_dynamic_workflow
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_dynamic_agent import ScriptProvider

ingestion_case = test_document_ingestion.ingestion_case
dynamic_case = test_dynamic_workflow.dynamic_case


def conversation(client, case):
    response = client.post(f"/api/v1/cases/{case}/conversations")
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(client, conv, text, key, revision=0):
    response = client.post(f"/api/v1/conversations/{conv}/messages", headers={"Idempotency-Key": key},
        json={"content": text, "operation": "assistant", "expected_revision": revision})
    assert response.status_code == 202, response.text
    return response.json()


def execute(engine, receipt, provider):
    provider.context_window = 1000000
    return execute_run(engine, receipt["run_id"], receipt["attempt_id"], provider, heartbeat_interval_seconds=60)


def manifest(engine, receipt):
    with engine.connect() as connection:
        return connection.scalar(select(agent_runs.c.context_manifest_json).where(agent_runs.c.id == receipt["run_id"]))


def test_new_conversation_gets_short_original_goal_and_bound_readback(ingestion_case):
    client, engine, _, case = ingestion_case
    old = conversation(client, case)
    first = send(client, old, "采购目标：先核实交货地点", "old")
    assert execute(engine, first, Decisions(("chat", {"answer": "请补充地点。"}))).status == "completed"
    new = conversation(client, case)
    receipt = send(client, new, "回顾一下之前的目标", "new")
    provider = Decisions(("read_task_history", {"message_ids": [first["message_id"]], "part": "question"}),
        ("chat", {"answer": "之前讨论过交货地点，请确认本次要核实的内容。"}))
    report = execute(engine, receipt, provider)
    assert report.status == "completed" and report.model_calls == 2
    assert "采购目标：先核实交货地点" in provider.messages[0][0].content
    observation = json.loads(next(m.content for m in provider.messages[1] if m.type == "tool"))
    assert "采购目标：先核实交货地点" in json.dumps(observation, ensure_ascii=False)
    handoff = manifest(engine, receipt)["task_handoff"]
    assert handoff["sources"][0]["conversation_id"] == old
    assert "先核实交货地点" not in json.dumps(manifest(engine, receipt), ensure_ascii=False)
    payload = client.get(f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result").json()["result"]
    assert payload["candidate"] is None and payload["kind"] == "chat"
    assert client.get(f"/api/v1/agent-runs/{receipt['run_id']}/checks").json()["checks"] == []


def test_history_not_in_handoff_whitelist_cannot_be_read(ingestion_case):
    client, engine, _, case = ingestion_case
    old = conversation(client, case)
    first = send(client, old, "历史讨论", "old")
    assert execute(engine, first, Decisions(("chat", {"answer": "请继续。"}))).status == "completed"
    new = conversation(client, case)
    receipt = send(client, new, "回查", "new")
    report = execute(engine, receipt, Decisions(("read_task_history", {"message_ids": [receipt["message_id"]]})))
    assert report.status == "partial" and report.model_calls == 1
    assert client.get(f"/api/v1/agent-runs/{receipt['run_id']}/checks").json()["checks"] == []


def test_retry_reuses_frozen_handoff_even_after_later_conversation(ingestion_case):
    class Unknown(Decisions):
        def decide(self, *_, **__):
            raise ProviderError("timeout", outcome_unknown=True)
    client, engine, _, case = ingestion_case
    old = conversation(client, case)
    first = send(client, old, "旧目标位置", "old")
    assert execute(engine, first, Decisions(("chat", {"answer": "请补充。"}))).status == "completed"
    target = send(client, conversation(client, case), "继续历史讨论", "target")
    assert execute(engine, target, Unknown()).status == "partial"
    with engine.connect() as connection:
        frozen = connection.execute(select(conversation_summaries).where(conversation_summaries.c.summary_json["source_run_id"].as_string() == target["run_id"])).mappings().all()
    later = send(client, conversation(client, case), "未来目标不能进入旧重试", "future")
    assert execute(engine, later, Decisions(("chat", {"answer": "请确认。"}))).status == "completed"
    retried = client.post(f"/api/v1/agent-runs/{target['run_id']}/retry", headers={"Idempotency-Key": "retry"}, json={"expected_revision": 0})
    assert retried.status_code == 202, retried.text
    provider = Decisions(("chat", {"answer": "请确认接下来要讨论的事项。"}))
    assert execute(engine, retried.json() | {"run_id": target["run_id"]}, provider).status == "completed"
    assert "旧目标位置" in provider.messages[0][0].content and "未来目标不能进入旧重试" not in provider.messages[0][0].content
    with engine.connect() as connection:
        after = connection.execute(select(conversation_summaries).where(conversation_summaries.c.summary_json["source_run_id"].as_string() == target["run_id"])).mappings().all()
    assert frozen == after


def test_old_goal_does_not_override_current_confirmed_rule(dynamic_case):
    client, engine, _case, _snapshot, old = dynamic_case
    first = send(client, old, "历史讨论数量999箱，尚未确认", "old", 3)
    assert execute(engine, first, Decisions(("chat", {"answer": "请先确认要求。"}))).status == "completed"
    confirmed, data = test_dynamic_workflow.confirm(dynamic_case, bindings=False)
    receipt = send(client, old, "现在已确认的数量要求是多少", "new-scope", 3)
    provider = Decisions(("read_checklist", {"query": "数量"}),
        ("answer_checklist", {"answer": "已确认数量要求为不低于10箱。", "criterion_ids": [data["criteria"][0]["criterion_id"]]}))
    report = execute(engine, receipt, provider)
    assert report.status == "completed", report
    assert "历史讨论数量999箱" in provider.messages[0][0].content
    layer = json.loads(provider.messages[0][0].content.split("\nMEMORY_DATA=", 1)[1])["task_handoff"]
    assert "checklist_version_id" in layer["changed_fields"]
    assert layer["current_scope"]["checklist_version_id"] == confirmed["confirmed_version"]["id"]
    payload = client.get(f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result").json()["result"]
    assert payload["checklist_answer"]["criteria"][0]["rule"]["value"] == "10"
    assert "999" not in report.answer_text and payload["candidate"] is None


def test_formal_dynamic_receives_no_task_handoff(dynamic_case):
    client, engine, case, _, old = dynamic_case
    first = send(client, old, "历史目标标记：核实数量", "old", 3)
    assert execute(engine, first, Decisions(("chat", {"answer": "请确认。"}))).status == "completed"
    test_dynamic_workflow.confirm(dynamic_case)
    class FormalProvider(ScriptProvider):
        def __init__(self):
            super().__init__()
            self.requests = []
        def decide(self, messages, schemas, **kwargs):
            self.requests.append((messages, schemas))
            return super().decide(messages, schemas, **kwargs)
        def extract(self, messages, schema, **kwargs):
            self.requests.append((messages, [schema]))
            return super().extract(messages, schema, **kwargs)
    receipt = send(client, conversation(client, case), "正式审查", "formal", 3)
    provider = FormalProvider()
    report = execute(engine, receipt, provider)
    assert report.status == "completed", report
    assert "历史目标标记" in provider.requests[0][0][0].content
    for messages, schemas in provider.requests[1:]:
        assert "task_handoff" not in json.dumps([m.content for m in messages], ensure_ascii=False)
        assert "read_task_history" not in json.dumps(schemas, ensure_ascii=False)
    assert manifest(engine, receipt)["task_handoff"]["sources"]


def test_uploaded_snapshot_changes_scope_while_old_original_remains_readable(ingestion_case):
    from backend.app.db.ingestion import DocumentIngestion

    client, engine, storage, case = ingestion_case
    conv = conversation(client, case)
    old = send(client, conv, "旧资料版本的讨论目标", "old")
    assert execute(engine, old, Decisions(("chat", {"answer": "请补充资料。"}))).status == "completed"
    uploaded = test_document_ingestion._upload(client, case, "vendor-a-quote.xlsx", "quote", 0, "upload", "supplier")
    DocumentIngestion(engine, storage).process_document(case, uploaded["snapshot_id"], uploaded["document_id"])
    receipt = send(client, conv, "看看之前讨论的目标", "new-snapshot", 1)
    provider = Decisions(("read_task_history", {"message_ids": [old["message_id"]], "part": "question"}),
                         ("chat", {"answer": "旧讨论可回看，当前资料需要重新核实。"}))
    report = execute(engine, receipt, provider)
    assert report.status == "completed", report
    layer = json.loads(provider.messages[0][0].content.split("\nMEMORY_DATA=", 1)[1])["task_handoff"]
    assert "snapshot_id" in layer["changed_fields"]
    assert layer["current_scope"]["snapshot_id"] == uploaded["snapshot_id"]
    assert layer["source_scope"]["snapshot_id"] != uploaded["snapshot_id"]
    observation = json.loads(next(m.content for m in provider.messages[1] if m.type == "tool"))
    assert observation["historical_context"][0]["snapshot_id"] == layer["source_scope"]["snapshot_id"]


def test_unsaved_candidate_is_not_loaded_as_new_conversation_definition(ingestion_case):
    client, engine, _, case = ingestion_case
    old = send(client, conversation(client, case), "想采购苹果，数量至少20箱", "proposal")
    patch = {"items": [{"item_id": "apple", "name": "苹果"}], "operations": [{
        "op": "add", "reason": "用户采购指令", "criterion": {"item_id": "apple", "name": "数量",
        "rule": {"type": "numeric", "operator": "gte", "value": "20", "unit": "箱"}}}]}
    assert execute(engine, old, Decisions(("propose_changes", patch))).status == "completed"
    assert client.get(f"/api/v1/agent-runs/{old['run_id']}/proposal").status_code == 200
    receipt = send(client, conversation(client, case), "回顾一下", "new")
    provider = Decisions(("chat", {"answer": "旧提议尚待保存确认，请说明本次需要。"}))
    assert execute(engine, receipt, provider).status == "completed"
    system = provider.messages[0][0].content
    base = json.loads(system.split("\n固定基准清单=", 1)[1].split("\n当前资料=", 1)[0])
    assert base is None or not base.get("criteria")
    assert "想采购苹果" in system and "已确认清单可读取=False" in system
    payload = client.get(f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result").json()["result"]
    assert payload["candidate"] is None and payload.get("checklist_answer") is None
