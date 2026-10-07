"""Workbench reads stay bound to the authorized Case and immutable Run snapshot."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, select, update

from backend.app.agent.fact_memory import encode_fact_sets
from backend.app.agent.session_memory import _repository_fingerprint
from backend.app.api.auth import AuthenticationError, Principal
from backend.app.api.main import create_app
from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.models import (
    agent_runs,
    cases,
    document_versions,
    evidence_blocks,
    metadata,
)
from backend.app.extraction.fields import extract_fields
from backend.app.extraction.memory import extract_memory
from backend.app.field_contract import FieldKey
from backend.app.parsing import parse_mineru_middle, verify_pdf_source_coverage
from backend.app.rules.fields import evaluate_field
from backend.app.rules.memory import RULE_VERSION, evaluate_memory

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


class DemoAuthenticator:
    enabled = True

    def authenticate(self, authorization):
        if authorization == "Bearer alice":
            return Principal("https://issuer.test", "alice")
        if authorization == "Bearer bob":
            return Principal("https://issuer.test", "bob")
        raise AuthenticationError()


def _fixture(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'workbench.db'}")
    metadata.create_all(engine)
    data_root = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(data_root))
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    alice = {"Authorization": "Bearer alice"}
    bob = {"Authorization": "Bearer bob"}
    project = client.post("/api/v1/projects", json={"name": "采购"}, headers=alice).json()
    case = client.post("/api/v1/cases", json={"project_id": project["id"]},
                       headers=alice).json()
    case_id = case["id"]
    source = (EXAMPLES / "vendor-a-quote.xlsx").read_bytes()
    upload = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={**alice, "Idempotency-Key": "upload-one"},
        data={"role": "quote", "supplier_handle": "vendor-a",
              "supplier_name": "Vendor A", "model_name": "Model-X1",
              "expected_revision": "0"},
        files={"file": ("vendor-a-quote.xlsx", source,
                        "application/octet-stream")},
    )
    assert upload.status_code == 201, upload.text
    receipt = upload.json()
    DocumentIngestion(engine, data_root).process_document(
        case_id, receipt["snapshot_id"], receipt["document_id"])
    return engine, client, alice, bob, case_id, receipt, source


def test_authorized_original_read_is_hash_checked_and_snapshot_scoped(tmp_path, monkeypatch):
    engine, client, alice, bob, case_id, receipt, source = _fixture(tmp_path, monkeypatch)
    url = (f"/api/v1/cases/{case_id}/snapshots/{receipt['snapshot_id']}"
           f"/documents/{receipt['document_id']}/original")
    response = client.get(url, headers=alice)
    assert response.status_code == 200
    assert response.content == source
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert response.headers["x-content-type-options"] == "nosniff"
    window_url = (f"/api/v1/cases/{case_id}/snapshots/{receipt['snapshot_id']}"
                  f"/documents/{receipt['document_id']}/original/sheet-window"
                  "?sheet=%E6%8A%A5%E4%BB%B7&cell=B6")
    window = client.get(window_url, headers=alice)
    assert window.status_code == 200, window.text
    cells = [cell for row in window.json()["rows"] for cell in row["cells"]]
    assert next(cell for cell in cells if cell["address"] == "B6") == {
        "address": "B6", "text": "已装内存 16 GB", "highlight": True,
    }
    assert client.get(window_url, headers=bob).status_code == 404
    assert client.get(window_url.replace("cell=B6", "cell=ZZ99999"),
                      headers=alice).status_code == 422
    assert client.get(f"/api/v1/cases/{case_id}/documents",
                      headers=alice).json()["documents"][0]["model_name"] == "Model-X1"
    assert client.get(url, headers=bob).status_code == 404
    assert client.get(url.replace(receipt["snapshot_id"], "wrong"),
                      headers=alice).status_code == 404

    pdf_source = (EXAMPLES / "requirements.pdf").read_bytes()
    pdf_upload = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={**alice, "Idempotency-Key": "upload-pdf"},
        data={"role": "requirement", "expected_revision": "1"},
        files={"file": ("requirements.pdf", pdf_source, "application/pdf")},
    )
    assert pdf_upload.status_code == 201, pdf_upload.text
    pdf = pdf_upload.json()
    pdf_response = client.get(
        f"/api/v1/cases/{case_id}/snapshots/{pdf['snapshot_id']}"
        f"/documents/{pdf['document_id']}/original", headers=alice)
    assert pdf_response.status_code == 200
    assert pdf_response.content == pdf_source
    assert pdf_response.headers["content-type"].startswith("application/pdf")
    assert client.get(window_url.replace(receipt["document_id"], pdf["document_id"]),
                      headers=alice).status_code == 404

    with engine.connect() as connection:
        key = connection.scalar(select(document_versions.c.storage_key).where(
            document_versions.c.id == receipt["document_id"]))
    path = tmp_path / "data" / key
    path.write_bytes(source + b"tampered")
    damaged = client.get(url, headers=alice)
    assert damaged.status_code != 200
    assert damaged.json()["error"]["code"] == "source_hash_mismatch"
    assert client.get(window_url, headers=alice).json()["error"]["code"] == "source_hash_mismatch"
    engine.dispose()


def test_check_results_are_structured_and_only_current_completed_is_formal(
        tmp_path, monkeypatch):
    engine, client, alice, bob, case_id, receipt, source = _fixture(tmp_path, monkeypatch)
    conversation = client.post(f"/api/v1/cases/{case_id}/conversations",
                               headers=alice).json()
    submitted = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={**alice, "Idempotency-Key": "question-one"},
        json={"content": "审查内存", "mode": "standard", "expected_revision": 1},
    ).json()
    run_id = submitted["run_id"]
    listed = client.get(f"/api/v1/cases/{case_id}/runs", headers=alice)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["runs"]] == [run_id]
    assert listed.json()["runs"][0]["question"] == "审查内存"
    assert client.get(f"/api/v1/cases/{case_id}/runs", headers=bob).status_code == 404
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == receipt["document_id"],
            evidence_blocks.c.cell == "B6"))
    repo = load_agent_snapshot(engine, case_id, receipt["snapshot_id"])
    memory = {
        "version": 2, "snapshot_id": receipt["snapshot_id"],
        "repository_fingerprint": _repository_fingerprint(repo),
        "fact_sets_available": False, "fact_sets": [],
        "check_results": [{
            "check_result_id": "verified-one", "snapshot_id": receipt["snapshot_id"],
            "supplier": "vendor-a", "field_key": "memory",
            "processing_status": "complete", "business_status": "满足",
            "reason_code": "threshold_met", "evidence_refs": [evidence_id],
            "reason_evidence_refs": [evidence_id], "fact_ids": [],
            "rule_version": RULE_VERSION,
        }],
    }
    url = f"/api/v1/agent-runs/{run_id}/checks"
    assert client.get(url, headers=bob).status_code == 404
    assert client.get(url, headers=alice).json()["checks"] == []
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            status="partial", memory_json=memory))
    partial = client.get(url, headers=alice).json()
    assert partial["checks"][0]["business_status"] == "满足"
    assert partial["checks"][0]["is_formal"] is False
    for nonformal_status in ("failed", "interrupted", "cancelled"):
        with engine.begin() as connection:
            connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
                status=nonformal_status))
        assert client.get(url, headers=alice).json()["checks"] == []
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            status="completed"))
    current = client.get(url, headers=alice).json()
    assert current["checks"][0]["is_formal"] is False
    assert current["checks"][0]["evidence_refs"] == [evidence_id]

    upload = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={**alice, "Idempotency-Key": "upload-two"},
        data={"role": "quote", "supplier_handle": "vendor-a",
              "supplier_name": "Vendor A", "model_name": "Model-X1",
              "expected_revision": "1"},
        files={"file": ("vendor-a-quote.xlsx", source,
                        "application/octet-stream")},
    )
    assert upload.status_code == 201, upload.text
    historical = client.get(url, headers=alice).json()
    assert historical["is_current"] is False
    assert historical["checks"][0]["is_formal"] is False

    memory["repository_fingerprint"] = sha256(b"wrong").hexdigest()
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            memory_json=memory))
    invalid = client.get(url, headers=alice)
    assert invalid.status_code != 200
    assert invalid.json()["error"]["code"] == "memory_scope"
    engine.dispose()


def test_formal_check_is_recomputed_from_source_bound_facts(tmp_path, monkeypatch):
    engine, client, alice, _, case_id, _, quote_bytes = _fixture(tmp_path, monkeypatch)
    requirement_bytes = (EXAMPLES / "requirements.pdf").read_bytes()
    spec_bytes = (EXAMPLES / "vendor-a-spec.pdf").read_bytes()
    latest = None
    for key, role, name, content, revision in (
        ("requirement", "requirement", "requirements.pdf", requirement_bytes, 1),
        ("spec", "spec", "vendor-a-spec.pdf", spec_bytes, 2),
    ):
        latest = client.post(
            f"/api/v1/cases/{case_id}/documents",
            headers={**alice, "Idempotency-Key": key},
            data={"role": role, "expected_revision": str(revision),
                  **({"supplier_handle": "vendor-a", "supplier_name": "Vendor A",
                      "model_name": "Model-X1"}
                     if role == "spec" else {})},
            files={"file": (name, content, "application/pdf")},
        )
        assert latest.status_code == 201, latest.text
    snapshot = latest.json()

    def cached_pdf(path, document_id):
        name = ("requirements.middle.json" if path.read_bytes() == requirement_bytes
                else "vendor-a-spec.middle.json")
        parsed = parse_mineru_middle(EXAMPLES / "parsed" / name,
                                     document_id=document_id)
        return verify_pdf_source_coverage(parsed, path)

    DocumentIngestion(engine, tmp_path / "data", pdf_parser=cached_pdf).process_snapshot(
        case_id, snapshot["snapshot_id"])
    repo = load_agent_snapshot(engine, case_id, snapshot["snapshot_id"])

    def model_call(operation, messages, schema):
        assert operation == "extract"
        facts = []
        by_id = {block.evidence_id: block for block in repo.blocks}
        for item in json.loads(messages[-1].content)["evidence"]:
            block = by_id[item["evidence_id"]]
            if "已装内存" not in block.text or "GB" not in block.text:
                continue
            facts.append({"raw_value": "32" if "32" in block.text else "16",
                          "raw_unit": "GB", "qualifier": "installed",
                          "comparator": "gte" if block.role == "requirement" else "eq",
                          "evidence_refs": [block.evidence_id]})
        return AIMessage(content="", tool_calls=[{
            "name": "MemoryCandidates", "args": {"facts": facts}, "id": "batch",
        }])

    fact_set = extract_memory(repo, "vendor-a", model_call)
    assert fact_set.complete
    result = evaluate_memory(repo, "vendor-a", list(fact_set.facts),
                             scan_complete=fact_set.complete)
    assert result.business_status == "资料冲突"

    def field_model_call(operation, messages, schema):
        assert operation == "extract"
        batch = json.loads(messages[-1].content)["evidence"]
        facts = []
        cases = (
            ("标称存储至少 512 GB", "storage", "512", "GB"),
            ("标称 SSD 1 TB", "storage", "1", "TB"),
            ("屏幕尺寸至少 14 英寸", "screen_size", "14", "英寸"),
            ("屏幕尺寸 14 英寸", "screen_size", "14", "英寸"),
            ("主机重量不超过 1.6 kg", "weight", "1.6", "kg"),
            ("主机重量 1.8 kg", "weight", "1.8", "kg"),
            ("质保期限至少 24 个月", "warranty", "24", "个月"),
            ("采购数量 10 台", "quantity", "10", "台"),
            ("含税单价不超过 6000 元/台", "unit_price", "6000", "元/台"),
            ("订单确认后 14 个自然日内交付", "delivery_days", "14", "自然日"),
        )
        for block in batch:
            for snippet, field, value, unit in cases:
                if snippet in block["text"]:
                    facts.append({"field_key": field, "raw_value": value,
                                  "raw_unit": unit,
                                  "evidence_refs": [block["evidence_id"]]})
        by_cell = {item["cell"]: item for item in batch if item["sheet"] == "报价"}
        for label, cell, field, value, unit in (
            ("A7", "B7", "quantity", "10", "台"),
            ("A8", "B8", "unit_price", "5800", "元/台"),
            ("A9", "B9", "delivery_days", "10", "工作日"),
        ):
            if label in by_cell and cell in by_cell:
                facts.append({"field_key": field, "raw_value": value,
                              "raw_unit": unit,
                              "evidence_refs": [by_cell[label]["evidence_id"],
                                                by_cell[cell]["evidence_id"]]})
        return AIMessage(content="", tool_calls=[{
            "name": "FieldCandidates", "args": {"facts": facts}, "id": "fields",
        }])

    field_keys = [field for field in FieldKey if field != FieldKey.MEMORY]
    extracted = extract_fields(repo, "vendor-a", field_keys, field_model_call)
    checks = [result] + [evaluate_field(
        repo, "vendor-a", field, extracted[field].facts,
        scan_complete=extracted[field].complete,
    ) for field in field_keys]
    conversation = client.post(f"/api/v1/cases/{case_id}/conversations",
                               headers=alice).json()
    submitted = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={**alice, "Idempotency-Key": "review"},
        json={"content": "核验全部八项", "mode": "thorough", "expected_revision": 3},
    )
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["run_id"]
    memory = {
        "version": 2, "snapshot_id": snapshot["snapshot_id"],
        "repository_fingerprint": _repository_fingerprint(repo),
        "fact_sets_available": True,
        "fact_sets": encode_fact_sets(repo, {
            item.fact_set_id: item for item in (fact_set, *extracted.values())}),
        "check_results": [{**asdict(check),
                           "evidence_refs": list(check.evidence_refs),
                           "reason_evidence_refs": list(check.reason_evidence_refs),
                           "fact_ids": list(check.fact_ids)} for check in checks],
    }
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            status="completed", memory_json=memory))
    url = f"/api/v1/agent-runs/{run_id}/checks"
    current = client.get(url, headers=alice)
    assert current.status_code == 200, current.text
    assert len(current.json()["checks"]) == 8
    assert all(check["is_formal"] for check in current.json()["checks"])
    assert {check["field_key"]: check["business_status"]
            for check in current.json()["checks"]} == {
                "memory": "资料冲突", "storage": "满足", "screen_size": "满足",
                "weight": "不满足", "warranty": "证据不足", "quantity": "满足",
                "unit_price": "满足", "delivery_days": "待人工确认",
            }

    # Deletion and upload can commit during source/rule verification. The
    # response must use the latest Case state, not the first sampled state.
    from backend.app.db import api_queries

    original_loader = api_queries.load_agent_snapshot
    deletion_committed = False

    def concurrent_deletion(*args):
        nonlocal deletion_committed
        if not deletion_committed:
            deletion_committed = True
            with engine.begin() as connection:
                connection.execute(update(cases).where(cases.c.id == case_id).values(
                    deletion_requested_at=datetime.now(UTC).replace(tzinfo=None)))
        return original_loader(*args)

    monkeypatch.setattr(api_queries, "load_agent_snapshot", concurrent_deletion)
    deleting_during_verification = client.get(url, headers=alice)
    assert deleting_during_verification.status_code == 409
    assert deleting_during_verification.json()["error"]["code"] == "case_deleting"
    with engine.begin() as connection:
        connection.execute(update(cases).where(cases.c.id == case_id).values(
            deletion_requested_at=None))

    uploaded = False

    def concurrent_upload(*args):
        nonlocal uploaded
        if not uploaded:
            uploaded = True
            replaced = client.post(
                f"/api/v1/cases/{case_id}/documents",
                headers={**alice, "Idempotency-Key": "quote-new"},
                data={"role": "quote", "supplier_handle": "vendor-a",
                      "supplier_name": "Vendor A", "model_name": "Model-X1",
                      "expected_revision": "3"},
                files={"file": ("vendor-a-quote.xlsx", quote_bytes,
                                "application/octet-stream")},
            )
            assert replaced.status_code == 201, replaced.text
        return original_loader(*args)

    monkeypatch.setattr(api_queries, "load_agent_snapshot", concurrent_upload)
    historical = client.get(url, headers=alice).json()
    assert historical["is_current"] is False
    assert all(not check["is_formal"] for check in historical["checks"])

    memory["check_results"][0]["business_status"] = "满足"
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            memory_json=memory))
    invalid = client.get(url, headers=alice)
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "memory_scope"
    with engine.begin() as connection:
        connection.execute(update(cases).where(cases.c.id == case_id).values(
            deletion_requested_at=datetime.now(UTC).replace(tzinfo=None)))
    deleting = client.get(url, headers=alice)
    assert deleting.status_code == 409
    assert deleting.json()["error"]["code"] == "case_deleting"
    engine.dispose()


def test_document_reads_expose_persisted_supplier_names(tmp_path, monkeypatch):
    engine, client, alice, _bob, case_id, receipt, _source = _fixture(tmp_path, monkeypatch)
    current = client.get(f"/api/v1/cases/{case_id}/documents", headers=alice)
    assert current.status_code == 200, current.text
    assert current.json()["documents"][0]["supplier_name"] == "Vendor A"
    history = client.get(
        f"/api/v1/cases/{case_id}/snapshots/{receipt['snapshot_id']}/documents",
        headers=alice)
    assert history.status_code == 200, history.text
    assert history.json()["documents"][0]["supplier_name"] == "Vendor A"
    engine.dispose()
