"""First HTTP slice: real SQL writes, idempotency and Run visibility."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert, update

from backend.app.api.main import create_app
from backend.app.db.models import agent_runs, cases, input_snapshots, metadata
from backend.app.db.run_repository import RunRepository
from backend.app.runtime.lifecycle import RunStatus


@pytest.fixture
def api(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    with TestClient(create_app(engine)) as client:
        yield client, engine
    engine.dispose()


def _case_and_conversation(client):
    created = client.post("/api/v1/cases")
    assert created.status_code == 201
    case = created.json()
    assert case["revision"] == 0
    conversation = client.post(f"/api/v1/cases/{case['id']}/conversations")
    assert conversation.status_code == 201
    return case["id"], conversation.json()["id"]


def _snapshot(engine, case_id):
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(insert(input_snapshots).values(
            id="snapshot-1", case_id=case_id, revision=1, created_at=now,
        ))
        connection.execute(update(cases).where(cases.c.id == case_id)
                           .values(revision=1, updated_at=now))


def _message(client, conversation_id, *, content="审查供应商 A", key="question-1"):
    return client.post(f"/api/v1/conversations/{conversation_id}/messages",
                       headers={"Idempotency-Key": key},
                       json={"content": content, "mode": "thorough",
                             "expected_revision": 1})


def test_conversation_and_messages_are_discoverable_after_api_restart(tmp_path):
    path = tmp_path / "restart.db"
    first_engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(first_engine, "connect")
    def enable_first_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(first_engine)
    with TestClient(create_app(first_engine)) as first_client:
        case_id, conversation_id = _case_and_conversation(first_client)
        second_conversation = first_client.post(
            f"/api/v1/cases/{case_id}/conversations",
        ).json()["id"]
        _snapshot(first_engine, case_id)
        receipt = _message(first_client, conversation_id).json()
        repository = RunRepository(first_engine)
        claimed = repository.claim(receipt["run_id"], receipt["attempt_id"])
        repository.finish(receipt["run_id"], receipt["attempt_id"],
                          claimed.fence, claimed.snapshot_id,
                          RunStatus.COMPLETED, "已完成审查")
    first_engine.dispose()

    second_engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(second_engine, "connect")
    def enable_second_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        with TestClient(create_app(second_engine)) as second_client:
            conversations = second_client.get(
                f"/api/v1/cases/{case_id}/conversations?limit=1",
            )
            assert conversations.status_code == 200, conversations.text
            page = conversations.json()
            assert page["case_id"] == case_id
            assert len(page["conversations"]) == 1
            assert page["next_offset"] == 1
            later = second_client.get(
                f"/api/v1/cases/{case_id}/conversations?limit=1&offset=1",
            ).json()
            assert {page["conversations"][0]["id"],
                    later["conversations"][0]["id"]} == {
                        conversation_id, second_conversation,
                    }
            messages = second_client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
            )
            assert messages.status_code == 200, messages.text
            item = messages.json()["messages"][0]
            assert item["content"] == "审查供应商 A"
            assert item["run_id"] == receipt["run_id"]
            assert second_client.get(
                f"/api/v1/agent-runs/{receipt['run_id']}",
            ).json()["answer_text"] == "已完成审查"
            replay = _message(second_client, conversation_id)
            assert replay.status_code == 202 and replay.json() == receipt
            assert len(second_client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
            ).json()["messages"]) == 1
            assert second_client.get(
                "/api/v1/cases/missing/conversations",
            ).status_code == 404
            assert second_client.get(
                "/api/v1/conversations/missing/messages",
            ).status_code == 404
    finally:
        second_engine.dispose()


def test_empty_case_refuses_question_and_errors_do_not_echo_content(api):
    client, _ = api
    case_id, conversation_id = _case_and_conversation(client)
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200

    waiting = _message(client, conversation_id)
    assert waiting.status_code == 409
    assert waiting.json()["error"]["code"] == "snapshot_not_ready"
    assert waiting.headers["X-Request-ID"] == waiting.json()["error"]["request_id"]

    invalid = client.post(f"/api/v1/conversations/{conversation_id}/messages",
                          json={"content": "private-question", "expected_revision": 1})
    assert invalid.status_code == 422
    assert "private-question" not in invalid.text
    assert invalid.json()["error"]["code"] == "validation_error"
    assert client.post("/api/v1/cases/missing/conversations").status_code == 404
    assert len(case_id) == 32


def test_submit_replay_conflict_run_events_and_cancel(api):
    client, engine = api
    case_id, conversation_id = _case_and_conversation(client)
    _snapshot(engine, case_id)

    first = _message(client, conversation_id)
    assert first.status_code == 202
    assert first.headers["Location"].endswith(first.json()["run_id"])
    replay = _message(client, conversation_id)
    assert replay.status_code == 202
    assert replay.json() == first.json()
    conflict = _message(client, conversation_id, content="审查供应商 B")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    run_id = first.json()["run_id"]
    run = client.get(f"/api/v1/agent-runs/{run_id}")
    assert run.status_code == 200
    assert run.json()["status"] == "queued"
    assert run.json()["is_current"] is True
    events = client.get(f"/api/v1/agent-runs/{run_id}/events")
    assert [item["seq"] for item in events.json()["events"]] == [1]
    assert client.get(f"/api/v1/agent-runs/{run_id}/events?after_seq=1").json()[
        "events"] == []

    cancelled = client.post(f"/api/v1/agent-runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/v1/agent-runs/{run_id}/events?after_seq=1").json()[
        "next_seq"] == 2


def test_retry_replays_one_attempt_and_stale_revision_cannot_create_new_run(api):
    client, engine = api
    case_id, conversation_id = _case_and_conversation(client)
    _snapshot(engine, case_id)
    first = _message(client, conversation_id).json()
    repository = RunRepository(engine)
    running = repository.claim(first["run_id"], first["attempt_id"])
    repository.interrupt(first["run_id"], first["attempt_id"], running.fence)

    url = f"/api/v1/agent-runs/{first['run_id']}/retry"
    retry = client.post(url, headers={"Idempotency-Key": "retry-1"},
                        json={"expected_revision": 1})
    assert retry.status_code == 202
    assert client.post(url, headers={"Idempotency-Key": "retry-1"},
                       json={"expected_revision": 1}).json() == retry.json()
    assert client.get(f"/api/v1/agent-runs/{first['run_id']}").json()[
        "active_attempt_id"] == retry.json()["attempt_id"]

    with engine.begin() as connection:
        connection.execute(update(cases).where(cases.c.id == case_id)
                           .values(revision=2))
    stale = _message(client, conversation_id, key="new-question")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"
    replay = _message(client, conversation_id)
    assert replay.status_code == 202
    assert replay.json() == first
    run = client.get(f"/api/v1/agent-runs/{first['run_id']}").json()
    assert run["is_current"] is False


def test_lost_worker_is_visible_as_unknown_and_retry_resets_active_attempt(api):
    client, engine = api
    case_id, conversation_id = _case_and_conversation(client)
    _snapshot(engine, case_id)
    submitted = _message(client, conversation_id).json()
    runs = RunRepository(engine)
    runs.claim(submitted["run_id"], submitted["attempt_id"])
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(
            agent_runs.c.id == submitted["run_id"]).values(
                updated_at=now - timedelta(minutes=3)))
    assert runs.recover_stale(now=now) == 1
    url = f"/api/v1/agent-runs/{submitted['run_id']}"
    lost = client.get(url).json()
    assert lost["status"] == "interrupted"
    assert lost["error_code"] == "worker_lost"
    assert lost["outcome_unknown"] is True
    retry = client.post(f"{url}/retry",
                        headers={"Idempotency-Key": "manual-after-loss"},
                        json={"expected_revision": 1})
    assert retry.status_code == 202
    active = client.get(url).json()
    assert active["status"] == "queued"
    assert active["outcome_unknown"] is False


def test_clarification_resume_accepts_reply_once_and_blocks_parallel_question(api):
    client, engine = api
    case_id, conversation_id = _case_and_conversation(client)
    _snapshot(engine, case_id)
    submitted = _message(client, conversation_id).json()
    runs = RunRepository(engine)
    claimed = runs.claim(submitted["run_id"], submitted["attempt_id"])
    from backend.app.runtime.lifecycle import RunStatus
    runs.finish(submitted["run_id"], submitted["attempt_id"], claimed.fence,
                "snapshot-1", RunStatus.WAITING_USER,
                answer_text="请说明供应商。")

    blocked = _message(client, conversation_id, key="another-question")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "conversation_busy"

    url = f"/api/v1/agent-runs/{submitted['run_id']}/resume"
    headers = {"Idempotency-Key": "clarification-1"}
    body = {"content": "供应商 A", "expected_revision": 1}
    resumed = client.post(url, headers=headers, json=body)
    assert resumed.status_code == 202
    assert resumed.json()["attempt_id"] != submitted["attempt_id"]
    assert client.post(url, headers=headers, json=body).json() == resumed.json()
    conflict = client.post(url, headers=headers,
                           json={"content": "供应商 B", "expected_revision": 1})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    first_page = client.get(
        f"/api/v1/conversations/{conversation_id}/messages?limit=1",
    ).json()
    second_page = client.get(
        f"/api/v1/conversations/{conversation_id}/messages?limit=1&offset=1",
    ).json()
    assert first_page["next_offset"] == 1
    assert second_page["next_offset"] is None
    assert [first_page["messages"][0]["content"],
            second_page["messages"][0]["content"]] == [
                "审查供应商 A", "供应商 A",
            ]
    assert {first_page["messages"][0]["run_id"],
            second_page["messages"][0]["run_id"]} == {submitted["run_id"]}
