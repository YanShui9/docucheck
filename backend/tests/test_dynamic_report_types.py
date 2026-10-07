"""Real upload/API contracts for nonnumeric report facts and corrections."""

import json

import pytest

from backend.app.criteria.facts import FactCandidate
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.run_executor import execute_run
from backend.tests import test_document_ingestion
from backend.tests.test_dynamic_agent import ScriptProvider, call
from backend.tests.test_dynamic_workflow import workbook

ingestion_case = test_document_ingestion.ingestion_case


@pytest.mark.parametrize("rule,name,text,value,alternative", [
    ({"type": "enum", "operator": "eq", "values": ["红色"]}, "颜色",
     "苹果 颜色红色或绿色", "红色", "绿色"),
    ({"type": "presence", "description": "检疫证明", "must_be_present": True}, "检疫证明",
     "苹果 未提供检疫证明", False, True),
    ({"type": "manual", "instruction": "人工确认储存方案"}, "储存方式",
     "苹果 储存方式冷藏或冷冻", "冷藏", "冷冻"),
])
def test_nonnumeric_facts_keep_confirmed_rule_types_and_source_constraints(
    ingestion_case, rule, name, text, value, alternative,
):
    client, engine, storage, case_id = ingestion_case
    uploaded = client.post(f"/api/v1/cases/{case_id}/documents",
        headers={"Idempotency-Key": "typed-upload"},
        data={"role": "quote", "supplier_handle": "vendor-a", "supplier_name": "供应商甲",
              "expected_revision": "0"},
        files={"file": ("苹果报价.xlsx", workbook(text), "application/octet-stream")})
    assert uploaded.status_code == 201, uploaded.text
    receipt = uploaded.json()
    snapshot = receipt["snapshot_id"]
    assert DocumentIngestion(engine, storage).process_document(
        case_id, snapshot, receipt["document_id"])["parse_status"] == "complete"
    definition = {
        "items": [{"item_id": "apple", "name": "苹果"}],
        "criteria": [{"criterion_id": "metric", "item_id": "apple", "name": name,
                      "rule": rule, "source": {"kind": "user_defined", "reason": "买方设置"}}],
    }
    checklist = f"/api/v1/cases/{case_id}/snapshots/{snapshot}/checklist"
    saved = client.put(checklist, headers={"Idempotency-Key": "typed-draft"}, json={
        **definition, "expected_revision": 1, "expected_checklist_revision": 0, "reason": "核对要求"})
    assert saved.status_code == 200, saved.text
    accepted = client.post(checklist + "/confirm", headers={"Idempotency-Key": "typed-confirm"}, json={
        "expected_revision": 1, "expected_checklist_revision": saved.json()["checklist_revision"],
        "criterion_ids": ["metric"], "confirmed": True, "reason": "确认要求"})
    assert accepted.status_code == 200, accepted.text
    conversation = client.post(f"/api/v1/cases/{case_id}/conversations").json()["id"]
    submitted = client.post(f"/api/v1/conversations/{conversation}/messages",
        headers={"Idempotency-Key": "typed-review"}, json={"content": "审查苹果", "expected_revision": 1})
    assert submitted.status_code == 202, submitted.text
    run_receipt = submitted.json()

    class TypedProvider(ScriptProvider):
        def extract(self, messages, schema, **options):
            data = json.loads(messages[-1].content)
            block = data["evidence"][0]
            return call(schema["function"]["name"], {"facts": [{
                "criterion_id": "metric", "item_id": "apple", "supplier": data["supplier"],
                "value": value, "unit": "", "quote": block["text"],
                "evidence_refs": [block["evidence_id"]],
            }]})

    run_id = run_receipt["run_id"]
    report = execute_run(engine, run_id, run_receipt["attempt_id"], TypedProvider(),
                         heartbeat_interval_seconds=60)
    assert report.status == "completed", report
    prefix = f"/api/v1/agent-runs/{run_id}"
    facts = client.get(prefix + "/facts").json()
    group = facts["fact_sets"][0]
    assert group["rule"] == accepted.json()["confirmed_version"]["definition"]["criteria"][0]["rule"]
    fact = group["facts"][0]
    assert fact["raw_value"] == value and type(fact["raw_value"]) is type(value)
    assert fact["raw_unit"] == ""
    candidate = {k: fact[k] for k in FactCandidate.model_fields}
    candidate["value"] = alternative
    corrected = client.post(prefix + "/fact-corrections",
        headers={"Idempotency-Key": "typed-correction"}, json={
            "fact_id": fact["fact_id"], "candidate": candidate, "reason": "核对原文备选",
            "expected_revision": 1, "expected_payload_digest": facts["payload_digest"],
        })
    if rule["type"] == "presence":
        # False is a valid boolean fact; changing it to unsupported True must fail business validation.
        assert corrected.status_code == 422, corrected.text
        assert corrected.json()["error"]["code"] == "unsupported_correction"
        assert client.get(prefix + "/facts").json()["payload_digest"] == facts["payload_digest"]
    else:
        assert corrected.status_code == 201, corrected.text
        audit = client.get(prefix + "/fact-corrections").json()
        assert audit["corrections"][0]["corrected_fact"]["value"] == alternative
        checks = client.get(prefix + "/checks").json()
        assert checks["payload_digest"] == audit["payload_digest"] == corrected.json()["payload_digest"]
        assert not any(c["is_formal"] for c in checks["checks"])
