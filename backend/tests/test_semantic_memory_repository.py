"""Semantic summaries preserve SQL authority, source phases, and retry fencing."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import func, insert, select, update

from backend.app.db.memory_repository import MemoryRepository
from backend.app.db.models import (
    agent_runs,
    assistant_runs,
    cases,
    conversation_summaries,
    messages,
    projects,
    run_events,
    task_memory,
)
from backend.app.db.run_repository import RunRepository, _now
from backend.app.runtime.lifecycle import RunStatus
from backend.tests import test_document_ingestion

ingestion_case = test_document_ingestion.ingestion_case


@pytest.fixture
def history(ingestion_case, request):
    client, engine, _, case = ingestion_case
    conversation = client.post(f"/api/v1/cases/{case}/conversations").json()["id"]

    def send(number):
        response = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            headers={"Idempotency-Key": f"semantic-{number}"},
            json={
                "content": f"第{number}轮采购需求：" + "保留完整原话。" * 15,
                "operation": "assistant",
                "expected_revision": 0,
            },
        )
        assert response.status_code == 202, response.text
        return response.json()

    receipts = []
    size = getattr(request, "param", 12)
    for number in range(size):
        receipt = send(number)
        # Build real API-submitted immutable phases without invoking the worker's
        # optional summarizer: these are repository authority tests.
        runs = RunRepository(engine)
        claimed = runs.claim(receipt["run_id"], receipt["attempt_id"])
        runs.finish(
            claimed.run_id,
            claimed.active_attempt_id,
            claimed.fence,
            claimed.snapshot_id,
            RunStatus.COMPLETED,
            answer_text="已记录，等待采购资料。" * 20,
            memory_json={"version": 5},
        )
        receipts.append(receipt)
    current = send(size)
    claim = RunRepository(engine).claim(current["run_id"], current["attempt_id"])
    return engine, case, conversation, claim, receipts, send


def repository(engine):
    from backend.app.db.semantic_memory_repository import SemanticMemoryRepository

    return SemanticMemoryRepository(engine)


def notes(view):
    return [
        {
            "kind": "goal",
            "text": "采购需求待补资料。",
            "source_message_ids": [view["sources"][0]["message_id"]],
        }
    ]


def test_prepare_readonly_save_and_legacy_fingerprint_stays_stable(history):
    engine, case, conversation, claim, receipts, _ = history
    service = repository(engine)
    legacy = MemoryRepository(engine).conversation(
        case, conversation, claim.snapshot_id
    )
    with engine.connect() as connection:
        count = connection.scalar(
            select(func.count()).select_from(conversation_summaries)
        )
    view = service.prepare(claim)
    assert view["pending_count"] == 4 and view["through_version"] == 4
    assert [s["message_id"] for s in view["sources"]] == [
        r["message_id"] for r in receipts[:4]
    ]
    assert all(
        s["question"] and s["answer"] and s["task_memory_id"] for s in view["sources"]
    )
    assert view == service.prepare(claim)
    with engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(conversation_summaries))
            == count
        )
    assert service.begin_attempt(claim, view) is True
    assert service.begin_attempt(claim, view) is False
    assert service.prepare(claim)["attempted"] is True
    summary = service.save(claim, view, notes(view))
    assert summary["notes"] == notes(view) and summary["through_version"] == 4
    assert service.validate(claim, view, summary) is None
    assert (
        MemoryRepository(engine).conversation(case, conversation, claim.snapshot_id)
        == legacy
    )
    with pytest.raises(ValueError, match="semantic_memory_changed"):
        service.save(claim, view, notes(view))
    with pytest.raises(ValueError, match="semantic_memory_changed"):
        service.validate(claim, view)


@pytest.mark.parametrize("target", ["message", "answer", "summary", "source_manifest"])
def test_saved_sources_and_payload_tampering_fail_closed(history, target):
    engine, _, _, claim, _, _ = history
    service = repository(engine)
    view = service.prepare(claim)
    summary = service.save(claim, view, notes(view))
    with engine.begin() as connection:
        if target == "message":
            connection.execute(
                update(messages)
                .where(messages.c.id == view["sources"][0]["message_id"])
                .values(content="tampered")
            )
        elif target == "answer":
            connection.execute(
                update(agent_runs)
                .where(agent_runs.c.id == view["sources"][0]["run_id"])
                .values(answer_text="tampered")
            )
        else:
            payload = deepcopy(
                connection.scalar(
                    select(conversation_summaries.c.summary_json).where(
                        conversation_summaries.c.id == summary["id"]
                    )
                )
            )
            if target == "summary":
                payload["notes"][0]["text"] = "tampered"
            else:
                payload["sources"][0]["message_id"] = "outside"
            connection.execute(
                update(conversation_summaries)
                .where(conversation_summaries.c.id == summary["id"])
                .values(summary_json=payload)
            )
    with pytest.raises(ValueError):
        service.prepare(claim)


@pytest.mark.parametrize(
    "invalid", ["unknown_ref", "kind", "long", "empty", "extra", "too_many", "oversize"]
)
def test_output_structure_and_refs_are_server_validated(history, invalid):
    engine, _, _, claim, _, _ = history
    service = repository(engine)
    view = service.prepare(claim)
    output = notes(view)
    if invalid == "unknown_ref":
        output[0]["source_message_ids"] = ["future-or-other-case"]
    elif invalid == "kind":
        output[0]["kind"] = "confirmed_fact"
    elif invalid == "long":
        output[0]["text"] = "字" * 301
    elif invalid == "empty":
        output = []
    elif invalid == "extra":
        output[0]["scope"] = "model-assigned"
    elif invalid == "too_many":
        output *= 9
    else:
        output = [dict(output[0], text="字" * 300) for _ in range(8)]
    with pytest.raises(ValueError, match="invalid_semantic_memory"):
        service.save(claim, view, output)


@pytest.mark.parametrize("change", ["fence", "attempt", "snapshot", "cancel"])
def test_claim_guards_and_old_view_cas(history, change):
    engine, _, _, claim, _, _ = history
    service = repository(engine)
    view = service.prepare(claim)
    if change == "cancel":
        with engine.begin() as connection:
            connection.execute(
                update(agent_runs)
                .where(agent_runs.c.id == claim.run_id)
                .values(status="cancelled")
            )
    else:
        claim = replace(
            claim,
            **{
                {
                    "fence": "fence",
                    "attempt": "active_attempt_id",
                    "snapshot": "snapshot_id",
                }[change]: 999 if change == "fence" else "wrong"
            },
        )
    for operation in (
        lambda: service.prepare(claim),
        lambda: service.begin_attempt(claim, view),
        lambda: service.save(claim, view, notes(view)),
        lambda: service.validate(claim, view),
    ):
        with pytest.raises(ValueError):
            operation()


def test_future_run_excluded_and_attempt_marker_has_only_server_fields(history):
    engine, _, _, claim, _, send = history
    service = repository(engine)
    original = service.prepare(claim)
    # Simulate a future phase already persisted while this worker retains its
    # original cutoff. The public submit guard normally serializes this.
    with engine.begin() as connection:
        connection.execute(
            update(agent_runs)
            .where(agent_runs.c.id == claim.run_id)
            .values(status="failed")
        )
    future = send(13)
    with engine.begin() as connection:
        connection.execute(
            update(agent_runs)
            .where(agent_runs.c.id == claim.run_id)
            .values(status="running")
        )
    runs = RunRepository(engine)
    future_claim = runs.claim(future["run_id"], future["attempt_id"])
    runs.finish(
        future_claim.run_id,
        future_claim.active_attempt_id,
        future_claim.fence,
        future_claim.snapshot_id,
        RunStatus.COMPLETED,
        answer_text="future",
        memory_json={"version": 5},
    )
    assert service.prepare(claim) == original
    assert service.begin_attempt(claim, original)
    with engine.connect() as connection:
        event = (
            connection.execute(
                select(run_events).where(
                    run_events.c.run_id == claim.run_id,
                    run_events.c.kind == "semantic_summary_started",
                )
            )
            .mappings()
            .one()
        )
    assert event["summary_json"] == {
        "version": 1,
        "input_digest": original["input_digest"],
    }


@pytest.mark.parametrize("history", [36], indirect=True)
def test_more_than_twenty_eight_phases_roll_forward_without_losing_oldest(history):
    engine, _, _, claim, receipts, _ = history
    service = repository(engine)
    for batch in range(7):
        view = service.prepare(claim)
        new_sources = [s for s in view["sources"] if s["version"] > batch * 4]
        assert [s["message_id"] for s in new_sources] == [
            r["message_id"] for r in receipts[batch * 4 : batch * 4 + 4]
        ]
        assert view["pending_count"] == 28 - batch * 4
        service.save(
            claim,
            view,
            [
                {
                    "kind": "goal",
                    "text": "下一批需求待补资料。",
                    "source_message_ids": [new_sources[-1]["message_id"]],
                }
            ],
        )
    assert service.prepare(claim)["pending_count"] == 0


def test_retry_never_registers_a_second_semantic_request(history):
    engine, _, _, claim, _, _ = history
    service = repository(engine)
    assert service.begin_attempt(claim, service.prepare(claim))
    runs = RunRepository(engine)
    runs.finish(
        claim.run_id,
        claim.active_attempt_id,
        claim.fence,
        claim.snapshot_id,
        RunStatus.FAILED,
        error_code="provider_outcome_unknown",
        outcome_unknown=True,
    )
    receipt = runs.retry(claim.run_id, claim.snapshot_id, 0, "retry-semantic")
    retried = runs.claim(receipt.run_id, receipt.attempt_id)
    view = service.prepare(retried)
    assert view["attempted"] is True
    assert service.begin_attempt(retried, view) is False


@pytest.mark.parametrize(
    "change", ["delete", "permission", "checklist", "base", "revision"]
)
def test_case_permission_and_binding_changes_block_all_operations(history, change):
    engine, case, _, claim, _, _ = history
    service = repository(engine)
    view = service.prepare(claim)
    with engine.begin() as connection:
        if change == "delete":
            connection.execute(
                update(cases)
                .where(cases.c.id == case)
                .values(deletion_requested_at=_now())
            )
        elif change == "permission":
            connection.execute(
                insert(projects).values(
                    id="revoked-project", name="revoked", created_at=_now()
                )
            )
            connection.execute(
                update(cases)
                .where(cases.c.id == case)
                .values(project_id="revoked-project")
            )
        elif change == "revision":
            connection.execute(
                update(cases).where(cases.c.id == case).values(revision=1)
            )
        else:
            connection.execute(
                update(assistant_runs)
                .where(assistant_runs.c.run_id == claim.run_id)
                .values(
                    **(
                        {"checklist_revision": 9}
                        if change == "checklist"
                        else {"base_digest": "0" * 64}
                    )
                )
            )
    for operation in (
        lambda: service.prepare(claim),
        lambda: service.begin_attempt(claim, view),
        lambda: service.save(claim, view, notes(view)),
        lambda: service.validate(claim, view),
    ):
        with pytest.raises(ValueError):
            operation()


def test_saved_view_validation_rejects_original_parent_summary_tampering(history):
    engine, _, _, claim, _, _ = history
    service = repository(engine)
    original = service.prepare(claim)
    parent = service.save(claim, original, notes(original))
    view = service.prepare(claim)
    summary = service.save(claim, view, notes(view))
    with engine.begin() as connection:
        row = connection.scalar(
            select(conversation_summaries.c.summary_json).where(
                conversation_summaries.c.id == parent["id"]
            )
        )
        changed = deepcopy(row)
        changed["notes"][0]["text"] = "parent tampered"
        connection.execute(
            update(conversation_summaries)
            .where(conversation_summaries.c.id == parent["id"])
            .values(summary_json=changed)
        )
    with pytest.raises(ValueError):
        service.validate(claim, view, summary)


def test_legacy_checkpoint_keeps_global_version_after_semantic_save(history):
    engine, case, conversation, claim, _, _ = history
    service = repository(engine)
    view = service.prepare(claim)
    summary = service.save(claim, view, notes(view))
    RunRepository(engine).finish(
        claim.run_id,
        claim.active_attempt_id,
        claim.fence,
        claim.snapshot_id,
        RunStatus.COMPLETED,
        answer_text="new response",
        memory_json={"version": 5},
    )
    legacy = MemoryRepository(engine).conversation(
        case, conversation, claim.snapshot_id
    )
    assert legacy["summary"]["kind"] == "source_excerpts_v1"
    assert legacy["summary"]["version"] > summary["version"]
    with engine.connect() as connection:
        versions = list(
            connection.scalars(
                select(conversation_summaries.c.version).where(
                    conversation_summaries.c.conversation_id == conversation
                )
            )
        )
    assert len(versions) == len(set(versions))


def test_future_summary_is_not_exposed_or_overwritten_by_an_older_worker(history):
    from backend.app.evidence.repository import digest

    engine, _, _, claim, _, _ = history
    service = repository(engine)
    original = service.prepare(claim)
    saved = service.save(claim, original, notes(original))
    view = service.prepare(claim)
    with engine.begin() as connection:
        row = dict(
            connection.execute(
                select(conversation_summaries).where(
                    conversation_summaries.c.id == saved["id"]
                )
            )
            .mappings()
            .one()
        )
        payload = deepcopy(row["summary_json"])
        payload["source_sequence"] = 999999
        payload["source_run_id"] = "future-run"
        payload.pop("payload_digest")
        payload["payload_digest"] = digest(payload)
        row.update(
            id="future-summary", version=row["version"] + 1, summary_json=payload
        )
        connection.execute(insert(conversation_summaries).values(**row))
    assert service.prepare(claim) == view
    with pytest.raises(ValueError, match="semantic_memory_changed"):
        service.save(claim, view, notes(view))


def test_mysql_isolation_adapter_precedes_guarded_reads_and_leaves_engine_default(
    history, monkeypatch
):
    """Adapter contract on SQLite; actual InnoDB concurrency is not asserted."""
    engine, _, _, claim, _, _ = history
    service = repository(engine)
    expected = service.prepare(claim)
    calls = []
    connection = engine.connect()

    def execution_options(**options):
        assert not connection.in_transaction()
        calls.append(options)
        return connection

    monkeypatch.setattr(connection, "execution_options", execution_options)
    service.engine = SimpleNamespace(
        dialect=SimpleNamespace(name="mysql"), connect=lambda: connection
    )
    assert service.prepare(claim) == expected
    assert calls == [{"isolation_level": "READ COMMITTED"}]
    with engine.connect() as fresh:
        assert fresh.get_isolation_level() == "SERIALIZABLE"


def test_metadata_size_cannot_make_a_longer_summary_look_like_compression(history):
    from backend.app.evidence.repository import digest

    engine, _, _, claim, _, _ = history
    service = repository(engine)
    original = service.prepare(claim)
    with engine.begin() as connection:
        for source in original["sources"]:
            state = deepcopy(
                connection.scalar(
                    select(task_memory.c.state_json).where(
                        task_memory.c.id == source["task_memory_id"]
                    )
                )
            )
            state.update(question_digest=digest("q"), answer_text="a")
            connection.execute(
                update(messages)
                .where(messages.c.id == source["message_id"])
                .values(content="q")
            )
            connection.execute(
                update(agent_runs)
                .where(agent_runs.c.id == source["run_id"])
                .values(answer_text="a")
            )
            connection.execute(
                update(task_memory)
                .where(task_memory.c.id == source["task_memory_id"])
                .values(state_json=state, memory_digest=digest(state))
            )
    view = service.prepare(claim)
    output = notes(view)
    output[0]["text"] = "longer" * 30
    with pytest.raises(ValueError, match="invalid_semantic_memory"):
        service.save(claim, view, output)


def test_same_user_message_can_have_distinct_immutable_response_phases(history):
    from backend.app.evidence.repository import digest

    engine, _, _, claim, _, _ = history
    service = repository(engine)
    initial = service.prepare(claim)
    first, second = initial["sources"][:2]
    with engine.begin() as connection:
        state = deepcopy(
            connection.scalar(
                select(task_memory.c.state_json).where(
                    task_memory.c.id == first["task_memory_id"]
                )
            )
        )
        state["focus"] = "later phase"
        state["checkpoint"]["focus"] = "later phase"
        connection.execute(
            update(task_memory)
            .where(task_memory.c.id == second["task_memory_id"])
            .values(
                source_run_id=first["run_id"],
                state_json=state,
                memory_digest=digest(state),
            )
        )
        connection.execute(
            update(agent_runs)
            .where(agent_runs.c.id == first["run_id"])
            .values(memory_json=state["checkpoint"])
        )
    view = service.prepare(claim)
    same_message = [
        s for s in view["sources"] if s["message_id"] == first["message_id"]
    ]
    assert len(same_message) == 2
    assert len({s["task_memory_id"] for s in same_message}) == 2
    assert len({s["phase_digest"] for s in same_message}) == 2
    summary = service.save(claim, view, notes(view))
    assert service.validate(claim, view, summary) is None
    assert len(service.prepare(claim)["sources"]) == 2
