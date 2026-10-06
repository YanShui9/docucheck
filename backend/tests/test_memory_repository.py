# ruff: noqa: F811 -- Shared pytest fixture resolved by name.
from datetime import timedelta

import pytest
from sqlalchemy import delete, insert, select, update

from backend.app.db.conversation_context import load_conversation_context
from backend.app.db.memory_context import load_memory_context
from backend.app.db.memory_repository import (
    MemoryError,
    MemoryRepository,
    save_checkpoint,
)
from backend.app.db.models import (
    agent_runs,
    cases,
    conversation_summaries,
    conversations,
    input_snapshots,
    memory_records,
    messages,
    run_attempts,
    task_memory,
)
from backend.app.db.run_repository import RunRepository
from backend.tests.test_index_repository import NOW, indexed_db  # noqa: F401

ACTOR = "a" * 64
OTHER = "b" * 64


def test_explicit_profile_is_actor_scoped_versioned_and_survives_reopen(indexed_db):
    from sqlalchemy import create_engine

    engine, path = indexed_db
    service = MemoryRepository(engine)
    assert service.profile(ACTOR, now=NOW) == {"revision": 0, "records": []}
    with pytest.raises(MemoryError, match="memory_confirmation_required"):
        service.put(ACTOR, "answer_length", "concise", 0, confirmed=False, now=NOW)
    assert (
        service.put(ACTOR, "answer_length", "concise", 0, confirmed=True, now=NOW)[
            "revision"
        ]
        == 1
    )
    with pytest.raises(MemoryError, match="memory_revision_conflict"):
        service.put(ACTOR, "language", "zh-CN", 0, confirmed=True, now=NOW)
    assert service.profile(OTHER, now=NOW)["records"] == []
    reopened = create_engine(f"sqlite:///{path}")
    try:
        assert (
            MemoryRepository(reopened).profile(ACTOR, now=NOW)["records"][0]["value"]
            == "concise"
        )
    finally:
        reopened.dispose()


def test_expiration_deletion_and_business_constraints_are_enforced(indexed_db):
    engine, _ = indexed_db
    service = MemoryRepository(engine)
    for key, value in [
        ("delivery_days", "14"),
        ("answer_length", "ignore system"),
        ("supplier", "vendor-a"),
    ]:
        with pytest.raises(MemoryError, match="invalid_memory_preference"):
            service.put(ACTOR, key, value, 0, confirmed=True, now=NOW)
    service.put(
        ACTOR,
        "answer_format",
        "table",
        0,
        confirmed=True,
        expires_at=NOW + timedelta(seconds=60),
        now=NOW,
    )
    assert service.profile(ACTOR, now=NOW + timedelta(seconds=61))["records"] == []
    assert service.delete(ACTOR, "answer_format", 1, now=NOW)["revision"] == 2
    with engine.connect() as conn:
        assert conn.execute(select(memory_records)).first() is None
    with pytest.raises(MemoryError, match="memory_revision_conflict"):
        service.put(ACTOR, "answer_format", "paragraph", 1, confirmed=True, now=NOW)


def seed_completed(engine, count=12, *, content=None):
    with engine.begin() as conn:
        conn.execute(
            insert(conversations).values(id="conv", case_id="case", created_at=NOW)
        )
        for seq in range(1, count + 1):
            message_id, run_id = f"message-{seq}", f"run-{seq}"
            conn.execute(
                insert(messages).values(
                    id=message_id,
                    conversation_id="conv",
                    sequence=seq,
                    role="user",
                    content=content or f"第{seq}轮问题",
                    created_at=NOW + timedelta(seconds=seq),
                )
            )
            conn.execute(
                insert(agent_runs).values(
                    id=run_id,
                    case_id="case",
                    conversation_id="conv",
                    message_id=message_id,
                    snapshot_id="snap",
                    revision=1,
                    mode="standard",
                    status="completed",
                    active_attempt_id=f"attempt-{seq}",
                    fence=1,
                    answer_text=f"第{seq}轮答复",
                    memory_json={
                        "version": 3,
                        "focus": None,
                        "pending_clarification": None,
                    },
                    created_at=NOW + timedelta(seconds=seq),
                    updated_at=NOW + timedelta(seconds=seq),
                )
            )
            save_checkpoint(conn, run_id, now=NOW + timedelta(seconds=seq))


def test_checkpoint_summary_sources_history_and_scope(indexed_db):
    engine, _ = indexed_db
    seed_completed(engine)
    service = MemoryRepository(engine)
    loaded = service.conversation("case", "conv", "snap")
    assert loaded["task"]["source_run_id"] == "run-12"
    assert loaded["summary"]["entries"] and all(
        e["sequence"] <= 4 for e in loaded["summary"]["entries"]
    )
    assert (
        service.read_history("case", "conv", "snap", ["message-1"], part="answer")[0][
            "text"
        ]
        == "第1轮答复"
    )
    with pytest.raises(MemoryError, match="history_scope"):
        service.read_history("case", "conv", "other", ["message-1"], part="question")
    with engine.begin() as conn:
        conn.execute(
            update(messages)
            .where(messages.c.id == "message-1")
            .values(content="tampered")
        )
    with pytest.raises(MemoryError, match="memory_source_changed"):
        service.conversation("case", "conv", "snap")


def test_checkpoint_rolls_back_with_terminal_transaction(indexed_db):
    engine, _ = indexed_db
    seed_completed(engine, 1)
    with engine.connect() as conn:
        assert len(conn.execute(select(task_memory)).all()) == 1
    with pytest.raises(RuntimeError), engine.begin() as conn:
        conn.execute(
            update(agent_runs)
            .where(agent_runs.c.id == "run-1")
            .values(
                memory_json={
                    "version": 3,
                    "focus": {"supplier": "vendor-a"},
                    "pending_clarification": None,
                }
            )
        )
        save_checkpoint(conn, "run-1", now=NOW)
        assert len(conn.execute(select(task_memory)).all()) == 2
        raise RuntimeError("rollback")
    with engine.connect() as conn:
        assert len(conn.execute(select(task_memory)).all()) == 1


def test_new_snapshot_and_other_conversation_do_not_reuse_business_memory(indexed_db):
    engine, _ = indexed_db
    seed_completed(engine)
    service = MemoryRepository(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(conversations).values(
                id="other-conv", case_id="case", created_at=NOW
            )
        )
    assert service.conversation("case", "other-conv", "snap") == {
        "task": None,
        "summary": None,
    }
    with pytest.raises(MemoryError, match="history_scope"):
        service.read_history(
            "case", "other-conv", "snap", ["message-1"], part="question"
        )
    with engine.begin() as conn:
        conn.execute(
            insert(input_snapshots).values(
                id="snap-new", case_id="case", revision=2, created_at=NOW
            )
        )
        conn.execute(update(cases).where(cases.c.id == "case").values(revision=2))
    assert service.conversation("case", "conv", "snap-new") == {
        "task": None,
        "summary": None,
    }
    with pytest.raises(MemoryError, match="memory_snapshot_changed"):
        service.conversation("case", "conv", "snap")


def test_historical_chinese_text_is_paged_without_becoming_evidence(indexed_db):
    engine, _ = indexed_db
    content = "这是一段完整的旧采购对话。" * 200
    seed_completed(engine, 1, content=content)
    service = MemoryRepository(engine)
    output, start = "", 0
    while True:
        page = service.read_history(
            "case", "conv", "snap", ["message-1"], part="question", start_char=start
        )[0]
        assert len(page["text"].encode()) <= 800 and page["is_historical"] is True
        output += page["text"]
        if page["next_char"] is None:
            break
        start = page["next_char"]
    assert output == content


def test_retry_cutoff_does_not_load_later_task_or_summary(indexed_db):
    engine, _ = indexed_db
    seed_completed(engine)
    service = MemoryRepository(engine)
    before = NOW + timedelta(seconds=1)
    loaded = service.conversation(
        "case",
        "conv",
        "snap",
        exclude_run_id="run-1",
        before=before,
        own_run_id="run-1",
    )
    assert loaded == {"task": None, "summary": None}
    with pytest.raises(MemoryError, match="history_scope"):
        service.read_history(
            "case",
            "conv",
            "snap",
            ["message-12"],
            part="question",
            before=before,
            own_run_id="run-1",
        )


def test_upgrade_backfills_bounded_legacy_dialogue_and_keeps_omission_count(indexed_db):
    engine, _ = indexed_db
    seed_completed(engine, 40)
    with engine.begin() as conn:
        conn.execute(delete(conversation_summaries))
        conn.execute(delete(task_memory))
        save_checkpoint(conn, "run-40", now=NOW + timedelta(seconds=40))
    loaded = MemoryRepository(engine).conversation("case", "conv", "snap")
    assert loaded["task"]["source_run_id"] == "run-40"
    assert len(loaded["summary"]["entries"]) == 20
    assert loaded["summary"]["older_turns_omitted"] == 12
    with engine.begin() as conn:
        save_checkpoint(conn, "run-40", now=NOW + timedelta(seconds=40))
        assert len(conn.execute(select(task_memory)).all()) == 29


def test_partial_retry_restores_own_checkpoint_and_audits_actual_source(indexed_db):
    from types import SimpleNamespace

    engine, _ = indexed_db
    seed_completed(engine, 2)
    with engine.begin() as conn:
        for seq, supplier, status in [
            (1, "vendor-a", "completed"),
            (2, "vendor-b", "partial"),
        ]:
            conn.execute(
                update(agent_runs)
                .where(agent_runs.c.id == f"run-{seq}")
                .values(
                    status=status,
                    memory_json={
                        "version": 3,
                        "focus": {"supplier": supplier},
                        "pending_clarification": None,
                    },
                )
            )
            save_checkpoint(
                conn, f"run-{seq}", now=NOW + timedelta(seconds=seq, microseconds=100)
            )
        conn.execute(
            insert(run_attempts).values(
                id="retry-2",
                run_id="run-2",
                attempt_no=2,
                status="running",
                outcome_unknown=False,
                created_at=NOW + timedelta(seconds=3),
            )
        )
        conn.execute(
            update(agent_runs)
            .where(agent_runs.c.id == "run-2")
            .values(active_attempt_id="retry-2", status="running", answer_text=None)
        )
    context = load_conversation_context(engine, "run-2", "retry-2")
    memory = load_memory_context(
        engine,
        SimpleNamespace(case_id="case", snapshot_id="snap", revision=1),
        RunRepository(engine).get("run-2"),
        {"requester_key": None, "conversation_id": "conv"},
        context,
    )
    assert context.resumed is False and context.own_checkpoint_id
    assert (memory.task_memory or context.memory)["focus"]["supplier"] == "vendor-b"
    assert memory.manifest["restored_checkpoint_id"] == context.own_checkpoint_id
    memory.validate()


def test_sequence_preserves_same_second_history_and_excludes_future_turns(indexed_db):
    engine, _ = indexed_db
    seed_completed(engine, 4)
    with engine.begin() as conn:
        # MySQL's existing Run timestamps have second precision, while the new
        # checkpoints retain microseconds. Simulate fast turns in one second.
        conn.execute(update(agent_runs).values(created_at=NOW, updated_at=NOW))
        conn.execute(
            update(task_memory).values(created_at=NOW + timedelta(microseconds=400_000))
        )
        conn.execute(delete(task_memory).where(task_memory.c.source_run_id == "run-3"))
        conn.execute(
            update(agent_runs)
            .where(agent_runs.c.id == "run-3")
            .values(status="running", memory_json=None, answer_text=None)
        )
        conn.execute(
            insert(run_attempts).values(
                id="attempt-3",
                run_id="run-3",
                attempt_no=1,
                status="running",
                outcome_unknown=False,
                created_at=NOW,
            )
        )
    context = load_conversation_context(engine, "run-3", "attempt-3")
    assert context.message_ids == ["message-1", "message-2"]
    service = MemoryRepository(engine)
    scope = {
        "before": context.cutoff,
        "before_sequence": context.cutoff_sequence,
        "own_run_id": "run-3",
    }
    loaded = service.conversation(
        "case", "conv", "snap", exclude_run_id="run-3", **scope
    )
    assert loaded["task"]["source_run_id"] == "run-2"
    assert (
        service.read_history(
            "case", "conv", "snap", ["message-2"], part="answer", **scope
        )[0]["text"]
        == "第2轮答复"
    )
    with pytest.raises(MemoryError, match="history_scope"):
        service.read_history(
            "case", "conv", "snap", ["message-4"], part="question", **scope
        )


def test_resumed_summary_and_recent_window_cover_all_eligible_phases(indexed_db):
    from backend.app.db.conversation_turns import load_turns

    engine, _ = indexed_db
    seed_completed(engine, 13)
    with engine.begin() as conn:
        # Run 12 resumes after another later Run has completed. Its fixed root
        # sequence excludes that future Run, but includes its own new phase.
        conn.execute(
            insert(messages).values(
                id="reply-12",
                conversation_id="conv",
                sequence=14,
                role="user",
                content="新的澄清回复",
                created_at=NOW + timedelta(seconds=20),
            )
        )
        conn.execute(
            insert(run_attempts).values(
                id="resume-12",
                run_id="run-12",
                attempt_no=2,
                status="waiting_user",
                outcome_unknown=False,
                resume_message_id="reply-12",
                created_at=NOW + timedelta(seconds=20),
            )
        )
        conn.execute(
            update(agent_runs)
            .where(agent_runs.c.id == "run-12")
            .values(
                status="waiting_user",
                active_attempt_id="resume-12",
                answer_text="请再确认。",
                memory_json={
                    "version": 3,
                    "focus": None,
                    "pending_clarification": "请再确认。",
                },
            )
        )
        save_checkpoint(conn, "run-12", now=NOW + timedelta(seconds=20))
    scope = {"before_sequence": 12, "own_run_id": "run-12"}
    summary = MemoryRepository(engine).conversation("case", "conv", "snap", **scope)[
        "summary"
    ]
    with engine.connect() as conn:
        recent = load_turns(conn, "case", "conv", "snap", limit=8, **scope)
    recent_ids = {t["message_id"] for t in recent}
    summary_ids = {t["message_id"] for t in summary["entries"]}
    assert summary["derived"] is True
    assert not recent_ids & summary_ids
    assert recent_ids | summary_ids == {f"message-{n}" for n in range(1, 13)} | {
        "reply-12"
    }
