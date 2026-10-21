"""Confirmed buyer rules have their own bounded, read-only answer path."""

import json
from copy import deepcopy

import pytest

from backend.app.agent.assistant import AssistantAgent
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.repository import EvidenceRepository
from backend.tests import test_dynamic_workflow
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_assistant_workflow import run, send
from backend.tests.test_dynamic_agent import definition

dynamic_case = test_dynamic_workflow.dynamic_case
ingestion_case = test_dynamic_workflow.ingestion_case


def price_definition():
    data = definition(metric="单价上限").model_dump(mode="json")
    data["criteria"][0].update(required=False, rule={"type": "money", "operator": "lte", "value": "25",
        "currency": "CNY", "per_unit": "箱", "tax_basis": "excluded"})
    return ChecklistDefinition.model_validate(data).model_dump(mode="json")


def answer_actions(text="苹果的单价上限为25 CNY/箱，不含税；该指标为可选项。"):
    return (("read_checklist", {"query": "单价"}),
            ("answer_checklist", {"answer": text, "criterion_ids": ["metric-1"]}))


def test_confirmed_price_answer_reads_full_rule_without_document_retrieval_or_edits():
    provider = Decisions(*answer_actions())
    confirmed = price_definition()
    draft = deepcopy(confirmed)
    draft["criteria"][0]["rule"]["value"] = "999"
    result = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider, definition=draft,
        confirmed_definition=confirmed, checklist_version_id="confirmed-v1").ask("清单里的苹果单价上限是多少？")
    assert result["status"] == "completed", result["error"]
    payload = result["payload"]
    assert payload["kind"] == "chat" and payload["candidate"] is None and not payload["citations"]
    assert "25 CNY/箱" in result["answer"] and "不含税" in result["answer"]
    assert "依据：已确认审查清单" in result["answer"] and "可选" in result["answer"]
    assert "confirmed-v1" not in result["answer"] and "metric-1" not in result["answer"]
    assert result["usage"]["model_calls"] == 2 and result["dynamic_payload"] is None
    observation = json.loads(provider.messages[1][-1].content)
    criterion = observation["criteria"][0]
    assert criterion["rule"] == confirmed["criteria"][0]["rule"]
    assert criterion["required"] is False and criterion["enabled"] is True
    assert criterion["conditions"] == [] and "source" not in criterion
    assert payload["checklist_answer"]["criteria"] == observation["criteria"]


@pytest.mark.parametrize("ref,version,text", [
    ("25", "confirmed-v1", "苹果单价上限为25 CNY/箱，不含税。"),
    ("1", "confirmed-v1", "苹果单价上限为25 CNY/箱，不含税。[1]"),
    ("metric-1", "25", "苹果单价上限为25 CNY/箱，不含税。[metric-1]"),
])
def test_short_identifiers_do_not_block_business_values_or_display_references(ref, version, text):
    confirmed = price_definition()
    confirmed["criteria"][0]["criterion_id"] = ref
    result = AssistantAgent(EvidenceRepository.empty("case", "snap"), Decisions(
        ("read_checklist", {"query": "单价"}),
        ("answer_checklist", {"answer": text, "criterion_ids": [ref]})),
        confirmed_definition=confirmed, checklist_version_id=version).ask("清单的单价上限？")
    assert result["status"] == "completed", result["error"]
    assert "25 CNY/箱" in result["answer"] and "不含税" in result["answer"]
    assert "[metric-1]" not in result["answer"]
    if "[" in text:
        assert "[1]" in result["answer"]


def test_unconfirmed_edit_base_is_never_used_as_confirmed_authority():
    provider = Decisions(("read_checklist", {"query": "单价"}))
    result = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider,
        definition=price_definition()).ask("已确认的单价上限？")
    assert result["status"] == "completed" and "尚无" in result["answer"]
    assert "已确认" in result["answer"] and "25" not in result["answer"]
    assert result["payload"].get("checklist_answer") is None


def test_disabled_and_conditional_rule_keeps_its_status_in_read_and_display():
    confirmed = price_definition()
    confirmed["criteria"][0].update(enabled=False, conditions=["仅限整箱采购"])
    provider = Decisions(*answer_actions("清单曾设置25 CNY/箱，不含税，现已停用。"))
    result = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider,
        confirmed_definition=confirmed, checklist_version_id="v1").ask("清单中的单价要求？")
    assert result["status"] == "completed", result["error"]
    assert "已停用" in result["answer"] and "仅限整箱采购" in result["answer"]
    source = result["payload"]["checklist_answer"]["criteria"][0]
    assert source["enabled"] is False and source["conditions"] == ["仅限整箱采购"]


def test_confirmed_read_can_page_to_late_rule_without_loading_edit_candidates():
    from backend.tests.test_assistant_stages import definition as large_definition

    base = large_definition(100, verbose=True)
    provider = Decisions(("read_checklist", {"query": "审查要求99"}),
        ("answer_checklist", {"answer": "该指标需人工核对供应商采购验收说明。", "criterion_ids": ["criterion-99"]}))
    result = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider,
        confirmed_definition=base, checklist_version_id="v1").ask("清单的审查要求99是什么？")
    assert result["status"] == "completed", result["error"]
    read = json.loads(provider.messages[-1][-1].content)
    assert [c["criterion_id"] for c in read["criteria"]] == ["criterion-99"]
    assert "需人工核对" in result["answer"]
    assert result["usage"]["model_calls"] == 2


def test_persisted_authority_remains_confirmed_while_a_new_draft_changes_the_rule(dynamic_case):
    confirmed, data = test_dynamic_workflow.confirm(dynamic_case, bindings=False)
    client, _, case_id, snapshot, _ = dynamic_case
    draft = deepcopy(data)
    draft["criteria"][0].update(rule={"type": "numeric", "operator": "gte", "value": "999", "unit": "箱"},
                                source={"kind": "user_defined", "reason": "未确认草稿"})
    response = client.put(f"/api/v1/cases/{case_id}/snapshots/{snapshot}/checklist",
        headers={"Idempotency-Key": "new-unconfirmed-draft"}, json={**draft, "expected_revision": 3,
        "expected_checklist_revision": confirmed["checklist_revision"], "reason": "草稿待确认"})
    assert response.status_code == 200, response.text
    target = data["criteria"][0]["criterion_id"]
    provider = Decisions(("read_checklist", {"query": "数量"}),
        ("answer_checklist", {"answer": "已确认的苹果数量要求为不低于10箱。", "criterion_ids": [target]}))
    receipt = send(dynamic_case, "已确认的数量要求？")
    report = run(dynamic_case, receipt, provider)
    assert report.status == "completed", report
    payload = client.get(f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result").json()["result"]
    assert payload["checklist_answer"]["criteria"][0]["rule"]["value"] == "10"
    assert "999" not in report.answer_text


@pytest.mark.parametrize("actions", [
    (("answer_checklist", {"answer": "25元", "criterion_ids": ["metric-1"]}),),
    (("read_checklist", {"query": "单价"}),
     ("answer_checklist", {"answer": "25元", "criterion_ids": ["other"]})),
])
def test_answer_cannot_reference_unread_or_unknown_rules(actions):
    result = AssistantAgent(EvidenceRepository.empty("case", "snap"), Decisions(*actions),
        confirmed_definition=price_definition(), checklist_version_id="v1").ask("单价？")
    assert result["status"] == "partial"
    assert result["payload"].get("checklist_answer") is None


def test_unmatched_confirmed_rule_gives_clear_feedback_without_more_decisions():
    provider = Decisions(("read_checklist", {"query": "不存在"}))
    result = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider,
        confirmed_definition=price_definition(), checklist_version_id="v1").ask("清单要求？")
    assert result["status"] == "completed" and "未匹配" in result["answer"]
    assert "25" not in result["answer"] and result["usage"]["model_calls"] == 1
    assert result["payload"].get("checklist_answer") is None


@pytest.mark.parametrize("tool,args", [("read_checklist", {"query": "单价"}),
                                       ("read_context", {"query": "单价"})])
def test_repeated_identical_reads_stop_with_an_actionable_message(tool, args):
    provider = Decisions(*[(tool, args)] * 8)
    result = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider, definition=price_definition(),
        confirmed_definition=price_definition(), checklist_version_id="v1").ask("单价上限？")
    assert result["error"] == "checklist_read_no_progress"
    assert result["usage"]["model_calls"] == 2
    assert "decision_budget" not in result["answer"] and "指标" in result["answer"]


@pytest.mark.parametrize("tamper", [None, "text", "rule", "version", "downgrade"])
def test_confirmed_answer_persistence_and_tamper_rejection(dynamic_case, tamper):
    from sqlalchemy import select, update

    from backend.app.db.models import agent_runs, assistant_runs
    from backend.app.evidence.repository import digest

    confirmed, data = test_dynamic_workflow.confirm(dynamic_case, bindings=False)
    target = data["criteria"][0]["criterion_id"]
    receipt = send(dynamic_case, "清单的数量要求是什么？")
    report = run(dynamic_case, receipt, Decisions(("read_checklist", {"query": "数量"}),
        ("answer_checklist", {"answer": "苹果采购数量要求不低于10箱。", "criterion_ids": [target]})))
    assert report.status == "completed", report
    client, engine, *_ = dynamic_case
    url = f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result"
    response = client.get(url)
    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    assert payload["checklist_answer"]["checklist_version_id"] == confirmed["confirmed_version"]["id"]
    assert payload["candidate"] is None and payload["citations"] == []
    if tamper is None:
        assert client.get(url).json() == response.json()
        return
    if tamper == "text":
        payload["checklist_answer"]["text"] = "999箱"
    elif tamper == "rule":
        payload["checklist_answer"]["criteria"][0]["rule"]["value"] = "999"
    elif tamper == "version":
        payload["checklist_answer"]["checklist_version_id"] = "other"
    else:
        payload.pop("checklist_answer")
    with engine.begin() as connection:
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt["run_id"]).values(
            payload_json=payload, payload_digest=digest(payload)))
        connection.execute(update(agent_runs).where(agent_runs.c.id == receipt["run_id"]).values(answer_text=payload["answer"]))
        checkpoint = connection.scalar(select(agent_runs.c.memory_json).where(agent_runs.c.id == receipt["run_id"]))
        assert checkpoint["checklist_answer_digest"]
    assert client.get(url).status_code == 409
