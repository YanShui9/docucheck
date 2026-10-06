# ruff: noqa: F811 -- Shared fixtures resolved by name.
from fastapi.testclient import TestClient
from sqlalchemy import insert, update

from backend.app.api.auth import Principal
from backend.app.api.main import create_app
from backend.app.db.models import cases, input_snapshots, project_members
from backend.app.db.workspace import WorkspaceRepository
from backend.tests.test_api_workspace_authorization import DemoAuthenticator
from backend.tests.test_checklists import definition
from backend.tests.test_index_repository import NOW, indexed_db  # noqa: F401

URL = "/api/v1/cases/case/snapshots/snap/checklist"


def test_api_confirmation_contract_and_project_roles(indexed_db):
    engine, _ = indexed_db
    actor = Principal("http://localhost:8080/realms/demo", "alice")
    project = WorkspaceRepository(engine).create_project(actor, "采购")
    with engine.begin() as conn:
        conn.execute(update(cases).values(project_id=project["id"]))
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    alice = {"Authorization": "Bearer alice", "Idempotency-Key": "save"}
    bob = {"Authorization": "Bearer bob", "Idempotency-Key": "save"}
    body = {
        **definition(),
        "expected_revision": 1,
        "expected_checklist_revision": 0,
        "reason": "买方设置",
    }
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers=bob).status_code == 404
    assert client.put(URL, json=body, headers=alice).status_code == 200
    assert client.put(URL, json=body, headers=bob).status_code == 404
    confirm = {
        "expected_revision": 1,
        "expected_checklist_revision": 1,
        "criterion_ids": ["rice-0"],
        "confirmed": True,
        "reason": "已核对",
    }
    for bad in (False, "true", 1):
        assert (
            client.post(
                URL + "/confirm", headers=alice, json={**confirm, "confirmed": bad}
            ).status_code
            == 422
        )
    accepted = client.post(URL + "/confirm", headers=alice, json=confirm)
    assert accepted.status_code == 200, accepted.text
    version_id = accepted.json()["confirmed_version"]["id"]
    version_url = f"/api/v1/cases/case/checklist-versions/{version_id}"
    assert client.get(version_url, headers=bob).status_code == 404
    assert client.get(version_url, headers=alice).status_code == 200
    with engine.begin() as conn:
        conn.execute(
            insert(project_members).values(
                project_id=project["id"],
                principal_key=Principal(actor.issuer, "bob").key,
                role="viewer",
                created_at=NOW,
            )
        )
    assert client.get(URL, headers=bob).status_code == 200
    assert client.put(URL, headers=bob, json=body).status_code == 404
    # Even an identical saved request cannot replay after editor access is revoked.
    with engine.begin() as conn:
        conn.execute(
            update(project_members)
            .where(project_members.c.principal_key == actor.key)
            .values(role="viewer")
        )
    assert client.put(URL, headers=alice, json=body).status_code == 404


def test_different_users_and_procurements_define_independent_fruit_clothing_and_electronics(
    indexed_db,
):
    engine, _ = indexed_db
    workspace = WorkspaceRepository(engine)
    alice = Principal("http://localhost:8080/realms/demo", "alice")
    bob = Principal(alice.issuer, "bob")
    projects = {
        "fruit": workspace.create_project(alice, "水果采购"),
        "clothing": workspace.create_project(bob, "服装采购"),
        "electronics": workspace.create_project(alice, "电子采购"),
    }
    with engine.begin() as conn:
        for category, project in projects.items():
            conn.execute(
                insert(cases).values(
                    id=category,
                    project_id=project["id"],
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            conn.execute(
                insert(input_snapshots).values(
                    id=category + "-snap", case_id=category, revision=1, created_at=NOW
                )
            )
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    examples = [
        (
            "fruit",
            "alice",
            "苹果",
            "甜度",
            {"type": "numeric", "operator": "gte", "value": "12", "unit": "°Bx"},
        ),
        (
            "clothing",
            "bob",
            "T恤",
            "面料",
            {"type": "enum", "operator": "eq", "values": ["纯棉"]},
        ),
        (
            "electronics",
            "alice",
            "显示器",
            "刷新率",
            {"type": "numeric", "operator": "gte", "value": "144", "unit": "Hz"},
        ),
    ]
    for case_id, user, item, name, rule in examples:
        url = f"/api/v1/cases/{case_id}/snapshots/{case_id}-snap/checklist"
        headers = {
            "Authorization": f"Bearer {user}",
            "Idempotency-Key": "same-key-each-case",
        }
        body = definition(1)
        body["items"][0]["name"] = item
        body["criteria"][0]["name"], body["criteria"][0]["rule"] = name, rule
        response = client.put(
            url,
            headers=headers,
            json={
                **body,
                "expected_revision": 1,
                "expected_checklist_revision": 0,
                "reason": "本次需求",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["draft"]["criteria"][0]["name"] == name
        result = client.post(
            url + "/confirm",
            headers=headers,
            json={
                "expected_revision": 1,
                "expected_checklist_revision": 1,
                "confirmed": True,
                "criterion_ids": ["rice-0"],
                "reason": "集中确认",
            },
        )
        assert result.status_code == 200, result.text
        other = "alice" if user == "bob" else "bob"
        assert (
            client.get(url, headers={"Authorization": f"Bearer {other}"}).status_code
            == 404
        )


def test_runner_rejects_dynamic_checklist_and_confirmation_rejects_active_run(
    uploaded_case,
):
    from backend.tests.test_persisted_agent_run import _submit_question

    client, engine, _storage, case_id, uploaded = uploaded_case
    submitted, conv = _submit_question(client, case_id)
    url = f"/api/v1/cases/{case_id}/snapshots/{uploaded['snapshot_id']}/checklist"
    body = {
        **definition(1),
        "expected_revision": 1,
        "expected_checklist_revision": 0,
        "reason": "新指标",
    }
    busy_save = client.put(url, headers={"Idempotency-Key": "draft"}, json=body)
    assert (
        busy_save.status_code == 409
        and busy_save.json()["error"]["code"] == "case_busy"
    )
    assert (
        client.post(f"/api/v1/agent-runs/{submitted['run_id']}/cancel").status_code
        == 200
    )
    assert (
        client.put(url, headers={"Idempotency-Key": "draft"}, json=body).status_code
        == 200
    )
    payload = {
        "expected_revision": 1,
        "expected_checklist_revision": 1,
        "criterion_ids": ["rice-0"],
        "confirmed": True,
        "reason": "确认",
    }
    from backend.app.db.models import agent_runs

    with engine.begin() as conn:
        # A late job from an older application must not race a confirmation.
        conn.execute(
            update(agent_runs)
            .where(agent_runs.c.id == submitted["run_id"])
            .values(status="running")
        )
    busy = client.post(
        url + "/confirm", headers={"Idempotency-Key": "busy"}, json=payload
    )
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "case_busy"
    assert (
        client.post(f"/api/v1/agent-runs/{submitted['run_id']}/cancel").status_code
        == 200
    )
    assert (
        client.post(
            url + "/confirm", headers={"Idempotency-Key": "confirm"}, json=payload
        ).status_code
        == 200
    )
    new = client.post(
        f"/api/v1/conversations/{conv}/messages",
        headers={"Idempotency-Key": "new"},
        json={"content": "核对这份新清单", "expected_revision": 1},
    )
    assert new.status_code == 202
    assert client.get(f"/api/v1/agent-runs/{new.json()['run_id']}").json()["workflow"] == "review"
    client.post(f"/api/v1/agent-runs/{new.json()['run_id']}/cancel")
    # Old failed runs also cannot start a new eight-field attempt on this Case.
    with engine.begin() as conn:
        conn.execute(
            update(agent_runs)
            .where(agent_runs.c.id == submitted["run_id"])
            .values(status="failed")
        )
    retry = client.post(
        f"/api/v1/agent-runs/{submitted['run_id']}/retry",
        headers={"Idempotency-Key": "retry-old"},
        json={"expected_revision": 1},
    )
    assert retry.status_code == 409, retry.text
    assert retry.json()["error"]["code"] == "checklist_execution_pending"


def test_case_cleanup_removes_checklists_versions_and_receipts(uploaded_case):
    from sqlalchemy import select

    from backend.app.db.case_cleanup import CaseCleanup
    from backend.app.db.case_deletion import CaseDeletion
    from backend.app.db.models import (
        checklist_heads,
        checklist_operations,
        checklist_versions,
    )

    client, engine, storage, case_id, uploaded = uploaded_case
    url = f"/api/v1/cases/{case_id}/snapshots/{uploaded['snapshot_id']}/checklist"
    body = {
        **definition(2),
        "expected_revision": 1,
        "expected_checklist_revision": 0,
        "reason": "删除验收",
    }
    assert (
        client.put(url, headers={"Idempotency-Key": "save"}, json=body).status_code
        == 200
    )
    # This creates both the current version and the remaining draft's base FK.
    assert (
        client.post(
            url + "/confirm",
            headers={"Idempotency-Key": "confirm"},
            json={
                "expected_revision": 1,
                "expected_checklist_revision": 1,
                "confirmed": True,
                "criterion_ids": ["rice-0"],
                "reason": "确认一项",
            },
        ).status_code
        == 200
    )
    CaseDeletion(engine).request(case_id, 1)
    cleaner = CaseCleanup(engine, storage)
    claim = cleaner.claim(case_id=case_id)
    assert claim and cleaner.process(claim)["status"] == "deleted"
    with engine.connect() as conn:
        for table in (checklist_heads, checklist_versions, checklist_operations):
            assert (
                conn.execute(select(table).where(table.c.case_id == case_id)).first()
                is None
            )


from backend.tests.test_persisted_agent_run import uploaded_case  # noqa: F401
