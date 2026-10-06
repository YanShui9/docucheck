"""The HTTP boundary must authorize nested resources by their Case."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import AuthenticationError, Principal
from backend.app.api.main import create_app
from backend.app.db.models import metadata


class DemoAuthenticator:
    enabled = True

    def authenticate(self, authorization):
        if authorization == "Bearer alice":
            return Principal("http://localhost:8080/realms/demo", "alice")
        if authorization == "Bearer bob":
            return Principal("http://localhost:8080/realms/demo", "bob")
        raise AuthenticationError()


def test_api_checks_case_and_nested_resource_membership():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    metadata.create_all(engine)
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    alice, bob = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}

    assert client.get("/api/v1/projects").status_code == 401
    project = client.post("/api/v1/projects", json={"name": "设备采购"},
                          headers=alice).json()
    assert client.post(f"/api/v1/projects/{project['id']}/members",
                       json={"subject": "alice", "role": "viewer"},
                       headers=alice).status_code == 409
    case = client.post("/api/v1/cases", json={"project_id": project["id"]},
                       headers=alice).json()
    case_id = case["id"]
    conversation = client.post(f"/api/v1/cases/{case_id}/conversations",
                               headers=alice).json()
    assert client.get("/api/v1/projects", headers=bob).json()["projects"] == []
    assert client.get(f"/api/v1/cases/{case_id}", headers=bob).status_code == 404
    assert client.get(f"/api/v1/cases/{case_id}/documents",
                      headers=bob).status_code == 404
    assert client.get(f"/api/v1/conversations/{conversation['id']}/messages",
                      headers=bob).status_code == 404
    assert client.get(f"/api/v1/projects/{project['id']}/cases",
                      headers=bob).status_code == 404

    assert client.post(f"/api/v1/projects/{project['id']}/members",
                       json={"subject": "bob", "role": "viewer"},
                       headers=alice).status_code == 201
    assert client.get(f"/api/v1/cases/{case_id}", headers=bob).status_code == 200
    assert client.get(f"/api/v1/conversations/{conversation['id']}/messages",
                      headers=bob).status_code == 200
    assert client.post(f"/api/v1/cases/{case_id}/conversations",
                       headers=bob).status_code == 404
    assert client.delete(f"/api/v1/cases/{case_id}?expected_revision=0",
                         headers=bob).status_code == 404
    engine.dispose()


def test_local_compatibility_mode_rejects_non_loopback_clients():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    metadata.create_all(engine)
    client = TestClient(create_app(engine), client=("192.0.2.10", 42340))
    response = client.post("/api/v1/cases")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "local_mode_loopback_only"
    engine.dispose()
