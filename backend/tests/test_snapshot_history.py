"""Historical document lists are immutable and scoped to their Case."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event

from backend.app.api.auth import AuthenticationError, Principal
from backend.app.api.main import create_app
from backend.app.db.models import metadata

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


class DemoAuthenticator:
    enabled = True

    def authenticate(self, authorization):
        if authorization == "Bearer alice":
            return Principal("http://localhost:8080/realms/demo", "alice")
        if authorization == "Bearer bob":
            return Principal("http://localhost:8080/realms/demo", "bob")
        raise AuthenticationError()


def test_snapshot_history_keeps_old_documents_and_enforces_membership(tmp_path,
                                                                     monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(tmp_path / "data"))
    alice = {"Authorization": "Bearer alice"}
    bob = {"Authorization": "Bearer bob"}
    with TestClient(create_app(engine, authenticator=DemoAuthenticator())) as client:
        project = client.post("/api/v1/projects", json={"name": "采购"},
                              headers=alice).json()
        case = client.post("/api/v1/cases",
                           json={"project_id": project["id"]}, headers=alice).json()
        case_id = case["id"]
        requirement = client.post(
            f"/api/v1/cases/{case_id}/documents",
            headers={**alice, "Idempotency-Key": "history-1"},
            data={"role": "requirement", "expected_revision": "0"},
            files={"file": ("requirements.pdf",
                            (EXAMPLES / "requirements.pdf").read_bytes(),
                            "application/pdf")},
        )
        assert requirement.status_code == 201, requirement.text
        quote = client.post(
            f"/api/v1/cases/{case_id}/documents",
            headers={**alice, "Idempotency-Key": "history-2"},
            data={"role": "quote", "expected_revision": "1",
                  "supplier_handle": "vendor-a", "supplier_name": "供应商 A"},
            files={"file": ("vendor-a-quote.xlsx",
                            (EXAMPLES / "vendor-a-quote.xlsx").read_bytes(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert quote.status_code == 201, quote.text

        history = client.get(f"/api/v1/cases/{case_id}/snapshots", headers=alice)
        assert history.status_code == 200, history.text
        assert [(item["revision"], item["document_count"])
                for item in history.json()["snapshots"]] == [(2, 2), (1, 1)]
        first_page = client.get(f"/api/v1/cases/{case_id}/snapshots?limit=1",
                                headers=alice).json()
        assert [item["revision"] for item in first_page["snapshots"]] == [2]
        assert first_page["next_before_revision"] == 2
        old_id = history.json()["snapshots"][1]["id"]
        old = client.get(f"/api/v1/cases/{case_id}/snapshots/{old_id}/documents",
                         headers=alice)
        assert old.status_code == 200, old.text
        assert old.json()["revision"] == 1
        assert [item["role"] for item in old.json()["documents"]] == ["requirement"]
        assert len(client.get(f"/api/v1/cases/{case_id}/documents",
                              headers=alice).json()["documents"]) == 2

        third = client.post(
            f"/api/v1/cases/{case_id}/documents",
            headers={**alice, "Idempotency-Key": "history-3"},
            data={"role": "spec", "expected_revision": "2",
                  "supplier_handle": "vendor-a", "supplier_name": "供应商 A"},
            files={"file": ("vendor-a-spec.pdf",
                            (EXAMPLES / "vendor-a-spec.pdf").read_bytes(),
                            "application/pdf")},
        )
        assert third.status_code == 201, third.text
        second_page = client.get(
            f"/api/v1/cases/{case_id}/snapshots?limit=1&before_revision=2",
            headers=alice,
        ).json()
        assert [item["revision"] for item in second_page["snapshots"]] == [1]

        assert client.get(f"/api/v1/cases/{case_id}/snapshots",
                          headers=bob).status_code == 404
        assert client.get(f"/api/v1/cases/{case_id}/snapshots/{old_id}/documents",
                          headers=bob).status_code == 404
        other = client.post("/api/v1/projects", json={"name": "别的项目"},
                            headers=bob).json()
        other_case = client.post("/api/v1/cases",
                                 json={"project_id": other["id"]},
                                 headers=bob).json()["id"]
        assert client.get(
            f"/api/v1/cases/{other_case}/snapshots/{old_id}/documents",
            headers=bob,
        ).status_code == 404
    engine.dispose()
