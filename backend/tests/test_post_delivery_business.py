"""Offline regressions for rule upgrades and corrections from eight-field reviews."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import insert, select, update

from backend.app.agent.fact_memory import decode_fact_sets
from backend.app.agent.graph import AgentSession
from backend.app.agent.session_memory import serialize_session
from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.correction_recheck import CorrectionRecheck
from backend.app.db.models import (
    agent_runs,
    cases,
    correction_reviews,
    document_versions,
    evidence_blocks,
)
from backend.app.db.run_executor import execute_run
from backend.app.extraction.fields import extract_fields
from backend.app.field_contract import FieldKey
from backend.app.rules.fields import (
    _evaluate_field_v1,
    _evaluate_field_v2,
    _evaluate_field_v3,
    evaluate_field,
)
from backend.tests.test_corrections import _request
from backend.tests.test_corrections import prepared as _prepared


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    yield from _prepared.__wrapped__(tmp_path, monkeypatch)


class FollowupProvider:
    model_id = "scripted-history-followup"
    context_window = 32768

    def __init__(self, field):
        self.field = field
        self.calls = 0

    def decide(self, messages, tools, **kwargs):
        import json

        self.calls += 1
        if self.calls == 1:
            name, args = "get_check_results", {"supplier": "vendor-a", "field_keys": [self.field]}
        else:
            name, args = "submit_answer", {"kind": "check",
                "check_result_ids": json.loads(messages[-1].content)["data"]["check_result_ids"]}
        return AIMessage(content="", tool_calls=[{
            "id": f"followup-{self.calls}", "name": name, "args": args,
        }], usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

    def extract(self, *args, **kwargs):
        raise AssertionError("verified facts should be reused without extraction")


def _memory_for_storage(engine, case_id, run_id, block, evaluator):
    with engine.connect() as connection:
        snapshot = connection.scalar(select(agent_runs.c.snapshot_id).where(agent_runs.c.id == run_id))
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        supplier_id = connection.scalar(select(document_versions.c.supplier_id).where(
            document_versions.c.snapshot_id == snapshot))
        for role, source_text in (("requirement", "标称存储至少 512 GB"), ("spec", "标称 SSD 512 GB")):
            document_id = uuid4().hex
            connection.execute(insert(document_versions).values(
                id=document_id, case_id=case_id, snapshot_id=snapshot, role=role,
                supplier_id=supplier_id if role == "spec" else None,
                original_name=f"{role}.pdf", storage_key=f"uploads/test/{role}.pdf",
                source_sha256="a" * 64, media_type="application/pdf", parser_version="test:1",
                parse_status="complete", coverage_json={"parser_version_full": "test:1", "total_pages": 1,
                    "processed_pages": [1], "coverage_issues": []}, created_at=now,
            ))
            connection.execute(insert(evidence_blocks).values(
                id=uuid4().hex, document_version_id=document_id, ordinal=0,
                source_text=source_text, page_idx=0, created_at=now,
            ))
    repo = load_agent_snapshot(engine, case_id, snapshot)

    def model_call(operation, messages, schema):
        facts = [{"field_key": "storage", "raw_value": "512", "raw_unit": "GB",
                  "evidence_refs": [item.evidence_id]} for item in repo.blocks]
        facts.append({"field_key": "storage", "raw_value": "1", "raw_unit": "TB",
                      "evidence_refs": [block.evidence_id]})
        return AIMessage(content="", tool_calls=[{
            "id": "storage", "name": "FieldCandidates", "args": {"facts": facts},
        }])

    fact_set = extract_fields(repo, "vendor-a", [FieldKey.STORAGE], model_call)[FieldKey.STORAGE]
    assert fact_set.complete
    session = AgentSession(repo, object())
    session.tools.fact_sets[fact_set.fact_set_id] = fact_set
    result = evaluator(repo, "vendor-a", FieldKey.STORAGE, fact_set.facts, scan_complete=True)
    saved = serialize_session(session, "历史核验结果")
    saved["check_results"] = [{**asdict(result), "evidence_refs": list(result.evidence_refs),
        "reason_evidence_refs": list(result.reason_evidence_refs), "fact_ids": list(result.fact_ids)}]
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(memory_json=saved))
    return saved


def _followup(client, engine, run_id, *, field, revision):
    with engine.connect() as connection:
        conversation_id = connection.scalar(select(agent_runs.c.conversation_id).where(agent_runs.c.id == run_id))
    submitted = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "history-followup"},
        json={"content": f"供应商 A 的{field}是否达标？请给出来源", "expected_revision": revision},
    )
    assert submitted.status_code == 202, submitted.text
    provider = FollowupProvider(field)
    receipt = submitted.json()
    result = execute_run(engine, receipt["run_id"], receipt["attempt_id"], provider)
    assert result.error_code is None
    assert provider.calls == 2
    return result


@pytest.mark.parametrize("evaluator", [_evaluate_field_v1, _evaluate_field_v2, _evaluate_field_v3])
def test_archived_rule_result_remains_readable_and_new_run_uses_current_rules(prepared, evaluator):
    client, engine, case_id, run_id, _, _, block = prepared
    saved = _memory_for_storage(engine, case_id, run_id, block, evaluator)
    archived = deepcopy(saved)
    original = saved["check_results"][0]
    checks = client.get(f"/api/v1/agent-runs/{run_id}/checks")
    assert checks.status_code == 200, checks.text
    assert checks.json()["is_current"] is True
    assert checks.json()["checks"] == [{**original, "is_formal": False}]
    exported = client.get(f"/api/v1/agent-runs/{run_id}/export?format=json")
    assert exported.status_code == 200, exported.text
    assert exported.json()["result"] == checks.json()
    following = _followup(client, engine, run_id, field="storage", revision=1)
    assert following.status == "completed"
    with engine.connect() as connection:
        assert connection.scalar(select(agent_runs.c.memory_json).where(agent_runs.c.id == run_id)) == archived
        current = connection.scalar(select(agent_runs.c.memory_json).where(agent_runs.c.id == following.run_id))
    updated = current["check_results"][0]
    assert updated["rule_version"] == "fields-v4"
    assert updated["check_result_id"] != original["check_result_id"]
    assert current["fact_sets"] == archived["fact_sets"]
    assert client.get(f"/api/v1/agent-runs/{following.run_id}/checks").json()["checks"][0]["is_formal"] is True


@pytest.mark.parametrize("evaluator", [_evaluate_field_v1, _evaluate_field_v2, _evaluate_field_v3])
@pytest.mark.parametrize("key,value", [("business_status", "满足"),
    ("check_result_id", "altered"), ("rule_version", "fields-unknown")])
def test_historical_read_and_export_still_reject_forged_or_unknown_rules(prepared, evaluator, key, value):
    client, engine, case_id, run_id, _, _, block = prepared
    saved = _memory_for_storage(engine, case_id, run_id, block, evaluator)
    saved["check_results"][0][key] = value
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(memory_json=saved))
    for suffix in ("checks", "export?format=json"):
        response = client.get(f"/api/v1/agent-runs/{run_id}/{suffix}")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "memory_scope"


def test_mixed_valid_versions_do_not_promote_old_result_through_current_field_pair(prepared):
    client, engine, case_id, run_id, _, _, block = prepared
    saved = _memory_for_storage(engine, case_id, run_id, block, _evaluate_field_v3)
    repo = load_agent_snapshot(engine, case_id, saved["snapshot_id"])
    fact_set = next(iter(decode_fact_sets(repo, saved["fact_sets"]).values()))
    current = evaluate_field(repo, "vendor-a", FieldKey.STORAGE, fact_set.facts, scan_complete=True)
    saved["check_results"].append({**asdict(current), "evidence_refs": list(current.evidence_refs),
        "reason_evidence_refs": list(current.reason_evidence_refs), "fact_ids": list(current.fact_ids)})
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(memory_json=saved))
    response = client.get(f"/api/v1/agent-runs/{run_id}/checks")
    assert response.status_code == 200, response.text
    checks = response.json()["checks"]
    assert [(item["rule_version"], item["is_formal"]) for item in checks] == [
        ("fields-v3", False), ("fields-v4", True)]
    assert client.get(f"/api/v1/agent-runs/{run_id}/export?format=json").json()["result"] == response.json()
    following = _followup(client, engine, run_id, field="storage", revision=1)
    assert following.status == "completed"
    with engine.connect() as connection:
        memory = connection.scalar(select(agent_runs.c.memory_json).where(agent_runs.c.id == following.run_id))
    assert len(memory["check_results"]) == 1 and memory["check_results"][0]["rule_version"] == "fields-v4"


@pytest.mark.parametrize("raw_unit,old_value,new_value,normalized_unit", [
    ("GB", "16", "32", "GB"), ("GiB", "16", "32", "GiB"),
    ("MiB", "16384", "32768", "GiB"),
])
def test_eight_field_memory_fact_can_correct_recheck_and_continue_agent(
    prepared, raw_unit, old_value, new_value, normalized_unit,
):
    client, engine, case_id, run_id, _, _, block = prepared
    with engine.begin() as connection:
        connection.execute(update(evidence_blocks).where(evidence_blocks.c.id == block.evidence_id).values(
            source_text=f"已装内存 {old_value} {raw_unit} 或 {new_value} {raw_unit}"))
        snapshot = connection.scalar(select(agent_runs.c.snapshot_id).where(agent_runs.c.id == run_id))
    repo = load_agent_snapshot(engine, case_id, snapshot)

    def model_call(operation, messages, schema):
        return AIMessage(content="", tool_calls=[{"id": "memory", "name": "MemoryCandidates", "args": {
            "facts": [{"raw_value": old_value, "raw_unit": raw_unit, "qualifier": "unknown",
                       "comparator": "eq", "evidence_refs": [block.evidence_id]}],
        }}])

    fact_set = extract_fields(repo, "vendor-a", [FieldKey.MEMORY], model_call)[FieldKey.MEMORY]
    old_fact = fact_set.facts[0]
    assert old_fact.value == 16 and old_fact.unit == normalized_unit
    assert fact_set.complete is False  # another value is still missing from extraction
    session = AgentSession(repo, object())
    session.tools.fact_sets[fact_set.fact_set_id] = fact_set
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            memory_json=serialize_session(session, "内存抽取待复核")))
    facts = client.get(f"/api/v1/agent-runs/{run_id}/facts")
    assert facts.status_code == 200, facts.text
    assert facts.json()["fact_sets"][0]["kind"] == "field"
    body = _request(case_id, run_id, fact_set.fact_set_id, old_fact.fact_id,
                    value=new_value, unit=raw_unit, reason="同一原文支持另一个内存容量，保留两个值待复核")
    url = f"/api/v1/cases/{case_id}/corrections"
    for value, code in (("2", "unsupported_correction"), (old_value, "correction_unchanged")):
        invalid = client.post(url, headers={"Idempotency-Key": f"invalid-{value}"},
                              json={**body, "raw_value": value})
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["error"]["code"] == code
    with engine.connect() as connection:
        assert connection.scalar(select(cases.c.revision).where(cases.c.id == case_id)) == 1
    submitted = client.post(url, headers={"Idempotency-Key": "memory-correction"}, json=body)
    assert submitted.status_code == 201, submitted.text
    receipt = submitted.json()
    reviewed = client.post(f"{url}/{receipt['correction_id']}/recheck")
    assert reviewed.status_code == 200, reviewed.text
    review = reviewed.json()
    assert review["review_status"] == "needs_review"
    assert review["corrected_fact"]["value"] == "32"
    assert review["corrected_fact"]["unit"] == normalized_unit
    assert review["check_result"]["business_status"] is None
    current_repo = load_agent_snapshot(engine, case_id, receipt["snapshot_id"])
    verified = CorrectionRecheck(engine).load_for_run(case_id, receipt["snapshot_id"], current_repo)
    assert verified["fact_set_json"]["kind"] == "field"
    assert {fact["value"] for fact in verified["fact_set_json"]["facts"]} == {"16", "32"}
    with engine.connect() as connection:
        audit = dict(connection.execute(select(correction_reviews)).mappings().one())
    following = _followup(client, engine, run_id, field="memory", revision=2)
    assert following.status == "partial"
    assert "人工复核" in following.answer_text
    checks = client.get(f"/api/v1/agent-runs/{following.run_id}/checks")
    assert checks.status_code == 200, checks.text
    assert checks.json()["checks"][0]["is_formal"] is False
    with engine.connect() as connection:
        assert dict(connection.execute(select(correction_reviews)).mappings().one()) == audit
