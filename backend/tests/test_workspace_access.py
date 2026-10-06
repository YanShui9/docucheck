"""Project membership is the Case authorization boundary."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import Principal
from backend.app.db.models import metadata
from backend.app.db.workspace import WorkspaceAccessError, WorkspaceRepository


def test_project_membership_scopes_cases_and_roles():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    metadata.create_all(engine)
    workspace = WorkspaceRepository(engine)
    alice = Principal("https://id.example/realm", "alice")
    bob = Principal("https://id.example/realm", "bob")

    project = workspace.create_project(alice, "笔记本采购")
    case = workspace.create_case(project["id"], alice)
    assert [item["id"] for item in workspace.list_projects(alice)] == [project["id"]]
    assert workspace.list_projects(bob) == []
    assert workspace.list_cases(project["id"], alice)[0]["id"] == case["id"]

    with pytest.raises(WorkspaceAccessError):
        workspace.require_case(case["id"], bob, "read")
    workspace.add_member(project["id"], alice, bob, "viewer")
    workspace.require_case(case["id"], bob, "read")
    with pytest.raises(WorkspaceAccessError):
        workspace.require_case(case["id"], bob, "write")
    with pytest.raises(WorkspaceAccessError):
        workspace.require_case(case["id"], bob, "owner")
    workspace.require_case(case["id"], alice, "owner")
    engine.dispose()


def test_project_cannot_lose_its_last_owner():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    metadata.create_all(engine)
    workspace = WorkspaceRepository(engine)
    alice = Principal("https://id.example/realm", "alice")
    bob = Principal("https://id.example/realm", "bob")
    project_id = workspace.create_project(alice, "采购")["id"]

    with pytest.raises(WorkspaceAccessError, match="last_project_owner"):
        workspace.add_member(project_id, alice, alice, "viewer")
    workspace.require_project(project_id, alice, "owner")
    workspace.add_member(project_id, alice, bob, "owner")
    workspace.add_member(project_id, alice, alice, "viewer")
    workspace.require_project(project_id, bob, "owner")
    with pytest.raises(WorkspaceAccessError, match="last_project_owner"):
        workspace.add_member(project_id, bob, bob, "editor")
    engine.dispose()
