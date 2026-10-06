"""Dynamic jobs must bind a user-confirmed definition and preserve old report scope."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.api.main import create_app
from backend.app.db.run_repository import RunRepository, SubmitRun
from backend.tests import test_index_repository
from backend.tests.test_checklists import confirm, put

indexed_db = test_index_repository.indexed_db


def test_dynamic_review_is_bound_to_confirmed_version_and_rejects_changed_definition(
    indexed_db,
):
    from backend.app.db.dynamic_repository import DynamicRepository
    from backend.app.db.models import dynamic_runs

    engine, _ = indexed_db
    client = TestClient(create_app(engine))
    conv = client.post("/api/v1/cases/case/conversations").json()["id"]
    put(engine)
    confirmed = confirm(engine)
    accepted = client.post(
        f"/api/v1/conversations/{conv}/messages",
        headers={"Idempotency-Key": "dynamic"},
        json={"content": "审查本次要求", "expected_revision": 1},
    )
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()
    with engine.connect() as conn:
        binding = conn.execute(select(dynamic_runs)).mappings().one()
    assert binding["checklist_version_id"] == confirmed["confirmed_version"]["id"]
    claim = RunRepository(engine).claim(run["run_id"], run["attempt_id"])
    service = DynamicRepository(engine)
    assert service.binding(claim)["workflow"] == "review"
    RunRepository(engine).cancel(run["run_id"])
    put(engine, revision=2, key="changed")
    client.post(
        f"/api/v1/agent-runs/{run['run_id']}/retry",
        headers={"Idempotency-Key": "stale"},
        json={"expected_revision": 1},
    )
    # Even repository callers cannot change the pinned definition silently.
    import pytest

    from backend.app.db.run_repository import RepositoryError

    with (
        engine.connect() as connection,
        pytest.raises(RepositoryError, match="checklist_changed"),
    ):
        service.validate_binding(connection, binding)


def test_proposal_operation_needs_no_confirmation_and_does_not_mutate_head(indexed_db):
    from backend.app.db.models import checklist_heads, dynamic_runs

    engine, _ = indexed_db
    client = TestClient(create_app(engine))
    conv = client.post("/api/v1/cases/case/conversations").json()["id"]
    submitted = client.post(
        f"/api/v1/conversations/{conv}/messages",
        headers={"Idempotency-Key": "proposal"},
        json={
            "content": "从需求整理审查清单",
            "operation": "propose_checklist",
            "expected_revision": 1,
        },
    )
    assert submitted.status_code == 202, submitted.text
    with engine.connect() as conn:
        assert conn.scalar(select(dynamic_runs.c.workflow)) == "proposal"
        assert conn.scalar(select(checklist_heads.c.case_id)) is None


def test_legacy_request_cannot_execute_a_dynamic_case(indexed_db):
    import pytest

    from backend.app.db.run_repository import RepositoryError

    engine, _ = indexed_db
    client = TestClient(create_app(engine))
    conv = client.post("/api/v1/cases/case/conversations").json()["id"]
    put(engine)
    confirm(engine)
    with pytest.raises(RepositoryError, match="checklist_execution_pending"):
        RunRepository(engine).submit(
            SubmitRun(
                "case", conv, "snap", 1, "核验", "standard", "legacy", workflow="legacy"
            )
        )
