"""One local, deterministic workbench journey through export and correction."""

import io
import json

from openpyxl import load_workbook

from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.run_executor import execute_run
from backend.app.parsing import parse_mineru_middle, verify_pdf_source_coverage
from backend.tests.test_m4_workbench_api import EXAMPLES, _fixture
from evals.m2_agent import ScriptedProvider, _call


class SourceFollowup:
    model_id = "scripted-m4-followup"
    context_window = 100_000

    def __init__(self):
        self.step = 0

    def decide(self, messages, tools, **kwargs):
        self.step += 1
        if self.step == 1:
            return _call("get_check_results", {"supplier": "vendor-a",
                        "field_keys": ["storage"]}, self.step)
        result = json.loads(messages[-1].content)["data"]
        return _call("submit_answer", {"kind": "check",
                      "check_result_ids": result["check_result_ids"],
                      "evidence_ids": []}, self.step)

    def extract(self, *args, **kwargs):
        raise AssertionError("followup_should_reuse_verified_results")


class CompleteDualProvider(ScriptedProvider):
    def extract(self, messages, schema, **kwargs):
        response = super().extract(messages, schema, **kwargs)
        if schema["function"]["name"] == "FieldCandidates":
            for block in json.loads(messages[-1].content)["evidence"]:
                if "标称 SSD 1 TB；标称 SSD 512 GB" in block["text"]:
                    response.tool_calls[0]["args"]["facts"].append({
                        "field_key": "storage", "raw_value": "512",
                        "raw_unit": "GB", "evidence_refs": [block["evidence_id"]],
                    })
        return response


def test_empty_case_to_sources_correction_and_exports(tmp_path, monkeypatch):
    engine, client, alice, bob, case_id, first, _ = _fixture(tmp_path, monkeypatch)
    assert first["revision"] == 1
    latest = first
    for key, role, name, expected_revision in (
        ("requirement", "requirement", "requirements.pdf", 1),
        ("spec", "spec", "vendor-a-spec.pdf", 2),
    ):
        response = client.post(
            f"/api/v1/cases/{case_id}/documents",
            headers={**alice, "Idempotency-Key": key},
            data={"role": role, "expected_revision": str(expected_revision),
                  **({"supplier_handle": "vendor-a", "supplier_name": "Vendor A",
                      "model_name": "Model-X1"} if role == "spec" else {})},
            files={"file": (name, (EXAMPLES / name).read_bytes(), "application/pdf")},
        )
        assert response.status_code == 201, response.text
        latest = response.json()

    workbook = load_workbook(EXAMPLES / "vendor-a-quote.xlsx")
    workbook.active["B10"] = "标称 SSD 1 TB；标称 SSD 512 GB"
    stream = io.BytesIO()
    workbook.save(stream)
    quote = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={**alice, "Idempotency-Key": "correction-quote"},
        data={"role": "quote", "supplier_handle": "vendor-a",
              "supplier_name": "Vendor A", "model_name": "Model-X1",
              "expected_revision": "3"},
        files={"file": ("vendor-a-quote.xlsx", stream.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert quote.status_code == 201, quote.text
    latest = quote.json()
    assert latest["revision"] == 4

    def cached_pdf(path, document_id):
        name = ("requirements.middle.json" if path.read_bytes() ==
                (EXAMPLES / "requirements.pdf").read_bytes() else
                "vendor-a-spec.middle.json")
        parsed = parse_mineru_middle(EXAMPLES / "parsed" / name,
                                     document_id=document_id)
        return verify_pdf_source_coverage(parsed, path)

    parsed = DocumentIngestion(engine, tmp_path / "data", pdf_parser=cached_pdf)
    receipts = parsed.process_snapshot(case_id, latest["snapshot_id"])
    assert all(item["parse_status"] == "complete" for item in receipts)
    conversation = client.post(f"/api/v1/cases/{case_id}/conversations",
                               headers=alice).json()
    review = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={**alice, "Idempotency-Key": "full-review"},
        json={"content": "请完整审查供应商 A 的八项指标", "mode": "thorough",
              "expected_revision": 4},
    )
    assert review.status_code == 202, review.text
    review_run = review.json()
    report = execute_run(engine, review_run["run_id"], review_run["attempt_id"],
                         CompleteDualProvider(("vendor-a",)))
    assert report.status == "completed", (report.error_code, report.answer_text,
                                           report.model_calls)
    run_id = review_run["run_id"]
    checks = client.get(f"/api/v1/agent-runs/{run_id}/checks", headers=alice).json()
    assert len(checks["checks"]) == 8
    assert all(item["is_formal"] for item in checks["checks"])
    export_url = f"/api/v1/agent-runs/{run_id}/export"
    assert client.get(f"{export_url}?format=xlsx", headers=bob).status_code == 404
    exported = client.get(f"{export_url}?format=json", headers=alice).json()
    assert exported["result"] == checks
    assert exported["sources"]
    source = next(item for item in exported["sources"]
                  if item["sheet"] == "报价" and item["cell"] == "B10")
    evidence = client.get(
        f"/api/v1/cases/{case_id}/snapshots/{latest['snapshot_id']}"
        f"/evidence/{source['evidence_id']}", headers=alice)
    assert evidence.status_code == 200
    assert evidence.json()["source_text"] == source["source_text"]
    assert client.get(f"{export_url}?format=xlsx", headers=alice).status_code == 200

    followup = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={**alice, "Idempotency-Key": "source-followup"},
        json={"content": "供应商 A 的存储依据是什么？", "mode": "standard",
              "expected_revision": 4},
    )
    assert followup.status_code == 202, followup.text
    followup_run = followup.json()
    asked = execute_run(engine, followup_run["run_id"],
                        followup_run["attempt_id"], SourceFollowup())
    assert asked.status == "completed", asked.error_code
    assert "存储" in asked.answer_text

    facts = client.get(f"/api/v1/agent-runs/{run_id}/facts", headers=alice).json()
    selected = next((fact_set, fact)
                    for fact_set in facts["fact_sets"]
                    if fact_set["field_key"] == "storage"
                    for fact in fact_set["facts"]
                    if fact["role"] == "quote" and fact["raw_value"] == "1")
    fact_set, fact = selected
    correction = client.post(
        f"/api/v1/cases/{case_id}/corrections",
        headers={**alice, "Idempotency-Key": "correct-storage"},
        json={"source_run_id": run_id, "fact_set_id": fact_set["fact_set_id"],
              "fact_id": fact["fact_id"], "raw_value": "512", "raw_unit": "GB",
              "reason": "同一原文也包含 512 GB", "expected_revision": 4},
    )
    assert correction.status_code == 201, correction.text
    receipt = correction.json()
    recheck = client.post(
        f"/api/v1/cases/{case_id}/corrections/{receipt['correction_id']}/recheck",
        headers=alice)
    assert recheck.status_code == 200, recheck.text
    assert recheck.json()["review_status"] == "needs_review"
    historical = client.get(f"{export_url}?format=json", headers=alice).json()
    assert historical["result"]["snapshot_id"] == latest["snapshot_id"]
    assert historical["result"]["is_current"] is False
    assert all(not item["is_formal"] for item in historical["result"]["checks"])
    engine.dispose()
