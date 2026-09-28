"""HTTP upload makes immutable input revisions from real PDF/XLSX files."""

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import create_engine, event, select

from backend.app.api.main import create_app
from backend.app.db.models import document_versions, input_snapshots, metadata

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


@pytest.fixture
def uploaded_api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'upload.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    storage = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(storage))
    with TestClient(create_app(engine)) as client:
        yield client, engine, storage
    engine.dispose()


def _case(client):
    response = client.post("/api/v1/cases")
    assert response.status_code == 201
    return response.json()["id"]


def _upload(client, case_id, *, name, content, role, revision,
            supplier=None, key="upload-1"):
    fields = {"role": role, "expected_revision": str(revision)}
    if supplier:
        fields.update(supplier_handle=supplier, supplier_name=supplier)
    return client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={"Idempotency-Key": key}, data=fields,
        files={"file": (name, content, "application/octet-stream")},
    )


def _pdf(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_upload_preserves_prior_snapshot_and_replay_is_stable(uploaded_api):
    client, engine, storage = uploaded_api
    case_id = _case(client)
    requirement = (EXAMPLES / "requirements.pdf").read_bytes()
    quote = (EXAMPLES / "vendor-a-quote.xlsx").read_bytes()
    first = _upload(client, case_id, name="requirements.pdf", content=requirement,
                    role="requirement", revision=0)
    assert first.status_code == 201, first.text
    assert first.json()["revision"] == 1
    assert first.json()["parse_status"] == "pending"
    assert first.json()["source_sha256"]
    assert _upload(client, case_id, name="requirements.pdf", content=requirement,
                   role="requirement", revision=0).json() == first.json()
    conversation = client.post(f"/api/v1/cases/{case_id}/conversations").json()
    message = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={"Idempotency-Key": "before-new-upload"},
        json={"content": "审查资料", "expected_revision": 1},
    )
    assert message.status_code == 202

    second = _upload(client, case_id, name="quote.xlsx", content=quote,
                     role="quote", supplier="vendor-a", revision=1, key="upload-2")
    assert second.status_code == 201, second.text
    assert second.json()["revision"] == 2
    listing = client.get(f"/api/v1/cases/{case_id}/documents")
    assert listing.status_code == 200
    assert listing.json()["revision"] == 2
    assert client.get(f"/api/v1/agent-runs/{message.json()['run_id']}").json()[
        "is_current"] is False
    assert {(d["role"], d["supplier_handle"]) for d in listing.json()["documents"]} == {
        ("requirement", None), ("quote", "vendor-a"),
    }
    with engine.connect() as connection:
        snapshots = connection.execute(select(input_snapshots).where(
            input_snapshots.c.case_id == case_id).order_by(input_snapshots.c.revision)
        ).mappings().all()
        old_docs = connection.execute(select(document_versions).where(
            document_versions.c.snapshot_id == snapshots[0]["id"])).mappings().all()
        new_docs = connection.execute(select(document_versions).where(
            document_versions.c.snapshot_id == snapshots[1]["id"])).mappings().all()
    assert len(old_docs) == 1 and len(new_docs) == 2
    assert old_docs[0]["id"] != next(
        d["id"] for d in new_docs if d["role"] == "requirement"
    )
    assert old_docs[0]["storage_key"] == next(
        d["storage_key"] for d in new_docs if d["role"] == "requirement"
    )
    assert len([p for p in (storage / "uploads").rglob("*.*") if p.is_file()]) == 2


def test_upload_rejects_changed_replay_stale_revision_and_unsafe_files(uploaded_api):
    client, engine, storage = uploaded_api
    case_id = _case(client)
    requirement = (EXAMPLES / "requirements.pdf").read_bytes()
    first = _upload(client, case_id, name="requirements.pdf", content=requirement,
                    role="requirement", revision=0)
    assert first.status_code == 201
    changed = _upload(client, case_id, name="requirements.pdf", content=_pdf(1),
                      role="requirement", revision=0)
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_conflict"
    stale = _upload(client, case_id, name="requirements.pdf", content=requirement,
                    role="requirement", revision=0, key="stale")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"
    invalid = [
        ("../unsafe.pdf", requirement, "requirement", None),
        ("bad.pdf", b"not-a-pdf", "requirement", None),
        ("bad.xlsx", b"not-a-zip", "quote", "vendor-a"),
        ("requirements.pdf", requirement, "quote", None),
        ("huge.pdf", b"%PDF-" + b"x" * (10 * 1024 * 1024), "requirement", None),
    ]
    for offset, (name, content, role, supplier) in enumerate(invalid):
        result = _upload(client, case_id, name=name, content=content,
                         role=role, supplier=supplier, revision=1,
                         key=f"invalid-{offset}")
        assert result.status_code in {400, 413, 422}, result.text
    assert _upload(client, "missing", name="requirements.pdf", content=requirement,
                   role="requirement", revision=0, key="other-case").status_code == 404
    with engine.connect() as connection:
        snapshots = connection.execute(select(input_snapshots).where(
            input_snapshots.c.case_id == case_id)).all()
    assert len(snapshots) == 1
    assert len([p for p in (storage / "uploads").rglob("*.*") if p.is_file()]) == 1


def test_replacing_role_preserves_old_version_and_pdf_page_limit(uploaded_api):
    client, engine, _ = uploaded_api
    case_id = _case(client)
    first = _upload(client, case_id, name="requirements.pdf", content=_pdf(20),
                    role="requirement", revision=0)
    assert first.status_code == 201, first.text
    too_many = _upload(client, case_id, name="spec.pdf", content=_pdf(11),
                       role="spec", supplier="vendor-a", revision=1, key="over-pages")
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "case_page_limit"
    replacement = _upload(client, case_id, name="requirements-v2.pdf",
                          content=_pdf(1), role="requirement", revision=1,
                          key="replace-requirement")
    assert replacement.status_code == 201, replacement.text
    listing = client.get(f"/api/v1/cases/{case_id}/documents").json()
    assert listing["revision"] == 2
    assert len(listing["documents"]) == 1
    assert listing["documents"][0]["id"] == replacement.json()["document_id"]
    with engine.connect() as connection:
        assert len(connection.execute(select(document_versions).where(
            document_versions.c.case_id == case_id)).all()) == 2


def test_upload_limits_active_suppliers_and_documents(uploaded_api):
    client, _, storage = uploaded_api
    case_id = _case(client)
    content = (EXAMPLES / "vendor-a-quote.xlsx").read_bytes()
    first = _upload(client, case_id, name="requirements.xlsx", content=content,
                    role="requirement", revision=0)
    assert first.status_code == 201
    revision = 1
    for supplier in ("vendor-a", "vendor-b", "vendor-c"):
        for role in ("quote", "spec"):
            uploaded = _upload(client, case_id, name=f"{supplier}-{role}.xlsx",
                               content=content, role=role, supplier=supplier,
                               revision=revision, key=f"{supplier}-{role}")
            assert uploaded.status_code == 201, uploaded.text
            revision += 1
    assert revision == 7
    extra = _upload(client, case_id, name="vendor-d-quote.xlsx", content=content,
                    role="quote", supplier="vendor-d", revision=7, key="fourth")
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "case_file_limit"
    assert client.get(f"/api/v1/cases/{case_id}/documents").json()[
        "revision"] == 7
    assert len([p for p in (storage / "uploads").rglob("*.*") if p.is_file()]) == 7
