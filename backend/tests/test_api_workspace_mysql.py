"""Optional real-MySQL authorization and migration check."""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete

from backend.app.api.auth import AuthenticationError, Principal
from backend.app.api.main import create_app
from backend.app.db.connection import database_url
from backend.app.db.models import cases, project_members, projects


class DemoAuthenticator:
    enabled = True

    def authenticate(self, authorization):
        if authorization == "Bearer alice":
            return Principal("http://127.0.0.1:8080/realms/docucheck-demo", "alice")
        if authorization == "Bearer bob":
            return Principal("http://127.0.0.1:8080/realms/docucheck-demo", "bob")
        raise AuthenticationError()


def test_project_case_isolation_on_mysql():
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL authorization")
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    alice, bob = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}
    project_id = case_id = None
    try:
        project_response = client.post("/api/v1/projects",
                                       json={"name": "M4 MySQL 隔离测试"}, headers=alice)
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]
        assert client.post(f"/api/v1/projects/{project_id}/members",
                           json={"subject": "alice", "role": "viewer"},
                           headers=alice).status_code == 409
        case_response = client.post("/api/v1/cases",
                                    json={"project_id": project_id}, headers=alice)
        assert case_response.status_code == 201
        case_id = case_response.json()["id"]
        assert client.get(f"/api/v1/cases/{case_id}", headers=alice).status_code == 200
        assert client.get(f"/api/v1/cases/{case_id}", headers=bob).status_code == 404
        assert client.get(f"/api/v1/cases/{case_id}/snapshots",
                          headers=alice).json()["snapshots"] == []
        assert client.get(f"/api/v1/projects/{project_id}/cases",
                          headers=bob).status_code == 404
    finally:
        if project_id is not None:
            with engine.begin() as connection:
                if case_id is not None:
                    connection.execute(delete(cases).where(cases.c.id == case_id))
                connection.execute(delete(project_members).where(
                    project_members.c.project_id == project_id,
                ))
                connection.execute(delete(projects).where(projects.c.id == project_id))
        engine.dispose()
