# ruff: noqa: F811 -- Imported pytest fixture is resolved by parameter name.
from fastapi.testclient import TestClient
from sqlalchemy import update

from backend.app.api.main import create_app
from backend.app.db.models import cases, document_index_jobs
from backend.tests.test_index_repository import indexed_db  # noqa: F401


def test_status_queue_no_model_required_and_revision_guard(indexed_db, monkeypatch):
    engine, _ = indexed_db
    monkeypatch.setenv("DOCUCHECK_VECTOR_RETRIEVAL_ENABLED", "0")
    client = TestClient(create_app(engine), client=("127.0.0.1", 50000))
    url = "/api/v1/cases/case/snapshots/snap/indexes"
    assert client.get(url).status_code == 200
    assert client.get(url).json()["documents"][0]["status"] == "not_scheduled"
    assert client.post(url, json={"expected_revision": 2}).status_code == 409
    assert client.post(url, json={"expected_revision": 1}).status_code == 202
    assert client.get(url).json()["documents"][0]["status"] == "pending"
    assert client.post(url, json={"expected_revision": 1}).json()["scheduled"] == 1
    assert client.get(url.replace("snap/indexes", "foreign/indexes")).status_code == 404
    with engine.begin() as conn:
        conn.execute(update(document_index_jobs).values(status="failed", attempts=3))
    assert client.post(url, json={"expected_revision": 1, "retry_failed": True}).status_code == 202
    with engine.begin() as conn:
        conn.execute(update(cases).values(deletion_requested_at=cases.c.created_at))
    assert client.get(url).status_code == 409


def test_index_api_checks_project_membership(indexed_db, monkeypatch):
    from backend.app.api.auth import Principal
    from backend.app.db.workspace import WorkspaceRepository
    from backend.tests.test_api_workspace_authorization import DemoAuthenticator
    engine, _ = indexed_db
    monkeypatch.setenv("DOCUCHECK_VECTOR_RETRIEVAL_ENABLED", "0")
    workspace = WorkspaceRepository(engine)
    actor = Principal("http://localhost:8080/realms/demo", "alice")
    project = workspace.create_project(actor, "采购")
    with engine.begin() as conn:
        conn.execute(update(cases).values(project_id=project["id"]))
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    url = "/api/v1/cases/case/snapshots/snap/indexes"
    assert client.get(url, headers={"Authorization": "Bearer bob"}).status_code == 404
    assert client.post(url, json={"expected_revision": 1}, headers={"Authorization": "Bearer bob"}).status_code == 404
    assert client.get(url, headers={"Authorization": "Bearer alice"}).status_code == 200
