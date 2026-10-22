"""Task handoff authority uses real temporary SQLite and API submitted phases."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select, update

from backend.app.db.models import (
    agent_runs,
    assistant_runs,
    cases,
    conversation_summaries,
    input_snapshots,
    messages,
    project_members,
    projects,
    run_attempts,
    run_events,
    task_memory,
)
from backend.app.db.run_repository import RunRepository, _now
from backend.app.evidence.repository import digest
from backend.app.runtime.lifecycle import RunStatus
from backend.tests import test_document_ingestion

ingestion_case = test_document_ingestion.ingestion_case


@pytest.fixture
def handoff(ingestion_case):
    client, engine, _, case = ingestion_case

    def conversation():
        return client.post(f"/api/v1/cases/{case}/conversations").json()["id"]

    def send(conv, text="采购目标：先核对资料再列待办", *, finish=True):
        response = client.post(
            f"/api/v1/conversations/{conv}/messages",
            headers={"Idempotency-Key": __import__("uuid").uuid4().hex},
            json={"content": text, "operation": "assistant", "expected_revision": 0},
        )
        assert response.status_code == 202, response.text
        receipt = response.json()
        runs = RunRepository(engine)
        claim = runs.claim(receipt["run_id"], receipt["attempt_id"])
        if finish:
            runs.finish(
                claim.run_id,
                claim.active_attempt_id,
                claim.fence,
                claim.snapshot_id,
                RunStatus.COMPLETED,
                answer_text="旧答案：999箱；不是当前规则。",
                memory_json={"version": 5},
            )
        return claim, receipt

    return engine, case, conversation, send


def repository(engine):
    from backend.app.db.task_handoff_repository import TaskHandoffRepository

    return TaskHandoffRepository(engine)


def test_short_conversation_captures_goal_and_latest_request_without_answers(handoff):
    engine, _, conversation, send = handoff
    old = conversation()
    _, goal = send(old, "先完成采购文档审查")
    _, recent = send(old, "请把缺少的资料列出来")
    claim, _ = send(conversation(), "继续此前任务", finish=False)
    service = repository(engine)
    view = service.prepare(claim)
    assert view["context"]["goal"]["text"] == "先完成采购文档审查"
    assert view["context"]["recent_request"]["text"] == "请把缺少的资料列出来"
    assert view["context"]["goal"]["message_id"] == goal["message_id"]
    assert view["context"]["recent_request"]["message_id"] == recent["message_id"]
    assert view["context"]["changed_fields"] == ["conversation_id"]
    assert "旧答案：999箱；不是当前规则。" not in str(view["context"])
    assert service.validate(claim, view) is None
    assert service.prepare(claim) == view
    pages = service.read_history(claim, view, [recent["message_id"]])
    assert {page["part"] for page in pages} == {"question", "answer"}
    assert all(page["conversation_id"] == old for page in pages)
    assert any("999" in page["text"] for page in pages)
    with engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(conversation_summaries))
            == 1
        )


def test_empty_and_same_scope_are_frozen_and_future_phases_never_appear(handoff):
    engine, _, conversation, send = handoff
    current = conversation()
    send(current)
    claim, _ = send(current, finish=False)
    service = repository(engine)
    view = service.prepare(claim)
    assert view["context"] is None
    send(conversation(), "未来记录不得追入")
    assert service.prepare(claim) == view
    assert service.validate(claim, view) is None


@pytest.mark.parametrize(
    "changed", ["snapshot_id", "base_digest", "checklist_revision"]
)
def test_same_conversation_changed_scope_captures_old_scope(handoff, changed):
    engine, case, conversation, send = handoff
    conv = conversation()
    send(conv)
    claim, _ = send(conv, finish=False)
    with engine.begin() as connection:
        if changed == "snapshot_id":
            snapshot = dict(
                connection.execute(
                    select(input_snapshots).where(
                        input_snapshots.c.id == claim.snapshot_id
                    )
                )
                .mappings()
                .one()
            )
            snapshot.update(id="new-snapshot", revision=1)
            connection.execute(insert(input_snapshots).values(**snapshot))
            connection.execute(
                update(cases).where(cases.c.id == case).values(revision=1)
            )
            connection.execute(
                update(agent_runs)
                .where(agent_runs.c.id == claim.run_id)
                .values(snapshot_id="new-snapshot", revision=1)
            )
            from backend.app.db.item_binding_repository import freeze_current

            frozen = freeze_current(connection, case, "new-snapshot", None, None)
            connection.execute(
                update(assistant_runs)
                .where(assistant_runs.c.run_id == claim.run_id)
                .values(
                    snapshot_id="new-snapshot", item_binding_digest=frozen["digest"]
                )
            )
            claim = replace(claim, snapshot_id="new-snapshot")
        else:
            # Historical bindings are immutable fingerprints, distinct from the
            # current binding, which is still validated by the production guard.
            old_run = connection.scalar(select(task_memory.c.source_run_id))
            values = (
                {"checklist_revision": 1}
                if changed == "checklist_revision"
                else {
                    "base_json": {"old": "draft"},
                    "base_digest": digest({"old": "draft"}),
                }
            )
            connection.execute(
                update(assistant_runs)
                .where(assistant_runs.c.run_id == old_run)
                .values(**values)
            )
    view = repository(engine).prepare(claim)
    assert changed in view["context"]["changed_fields"]
    assert view["context"]["recent_request"]["text"]


@pytest.mark.parametrize(
    "target",
    [
        "message",
        "phase",
        "record",
        "source_scope",
        "answer",
        "phase_time",
        "malformed_phase",
    ],
)
def test_captured_source_and_record_tampering_fail_closed(handoff, target):
    engine, _, conversation, send = handoff
    old_claim, old = send(conversation())
    claim, _ = send(conversation(), finish=False)
    service = repository(engine)
    view = service.prepare(claim)
    with engine.begin() as connection:
        if target == "message":
            connection.execute(
                update(messages)
                .where(messages.c.id == old["message_id"])
                .values(content="篡改")
            )
        elif target == "phase":
            connection.execute(
                update(task_memory)
                .where(task_memory.c.source_run_id == old_claim.run_id)
                .values(memory_digest="bad")
            )
        elif target == "phase_time":
            connection.execute(
                update(task_memory)
                .where(task_memory.c.source_run_id == old_claim.run_id)
                .values(created_at=_now() - timedelta(days=1))
            )
        elif target == "malformed_phase":
            connection.execute(
                update(task_memory)
                .where(task_memory.c.source_run_id == old_claim.run_id)
                .values(state_json=[])
            )
        elif target == "answer":
            connection.execute(
                update(agent_runs)
                .where(agent_runs.c.id == old_claim.run_id)
                .values(answer_text="篡改")
            )
        elif target == "source_scope":
            connection.execute(
                update(assistant_runs)
                .where(assistant_runs.c.run_id == old_claim.run_id)
                .values(base_digest="tampered")
            )
        else:
            row = connection.execute(select(conversation_summaries)).mappings().one()
            payload = deepcopy(row["summary_json"])
            payload["context"]["recent_request"]["text"] = "篡改"
            connection.execute(
                update(conversation_summaries)
                .where(conversation_summaries.c.id == row["id"])
                .values(summary_json=payload)
            )
    with pytest.raises(ValueError):
        service.validate(claim, view)
    with pytest.raises(ValueError):
        service.prepare(claim)


@pytest.mark.parametrize("target", ["cancel", "delete", "attempt", "fence", "member"])
def test_current_authority_guard_applies_to_prepare_validate_and_read(handoff, target):
    engine, case, conversation, send = handoff
    _, old = send(conversation())
    claim, _ = send(conversation(), finish=False)
    service = repository(engine)
    view = service.prepare(claim)
    with engine.begin() as connection:
        if target == "cancel":
            connection.execute(
                update(agent_runs)
                .where(agent_runs.c.id == claim.run_id)
                .values(status="cancel_requested")
            )
        elif target == "delete":
            connection.execute(
                update(cases)
                .where(cases.c.id == case)
                .values(deletion_requested_at=_now())
            )
        elif target == "attempt":
            connection.execute(
                update(run_attempts)
                .where(run_attempts.c.id == claim.active_attempt_id)
                .values(status="interrupted")
            )
        elif target == "fence":
            claim = replace(claim, fence=claim.fence + 1)
        else:
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
            connection.execute(delete(project_members))
    for action in [
        lambda: service.prepare(claim),
        lambda: service.validate(claim, view),
        lambda: service.read_history(claim, view, [old["message_id"]]),
    ]:
        with pytest.raises(ValueError):
            action()


def test_long_question_is_only_a_handle_and_complete_unicode_readback_is_paged(handoff):
    engine, _, conversation, send = handoff
    question = "完整原始问题不可截成完整目标。" * 100
    _, old = send(conversation(), question)
    claim, _ = send(conversation(), finish=False)
    service = repository(engine)
    view = service.prepare(claim)
    assert "text" not in view["context"]["recent_request"]
    assert (
        len(__import__("json").dumps(view["context"], ensure_ascii=False).encode())
        <= 3500
    )
    result, start = "", 0
    while True:
        page = service.read_history(
            claim, view, [old["message_id"]], part="question", start_char=start
        )[0]
        result += page["text"]
        if page["next_char"] is None:
            break
        start = page["next_char"]
    assert result == question
    with pytest.raises(ValueError, match="history_scope"):
        service.read_history(claim, view, [claim.run_id])


def test_other_case_and_future_phase_are_excluded(handoff):
    engine, _, conversation, send = handoff
    old_claim, _ = send(conversation())
    claim, receipt = send(conversation(), finish=False)
    with engine.begin() as connection:
        cutoff = connection.scalar(
            select(messages.c.created_at).where(messages.c.id == receipt["message_id"])
        )
        connection.execute(
            update(task_memory)
            .where(task_memory.c.source_run_id == old_claim.run_id)
            .values(created_at=cutoff + timedelta(seconds=10))
        )
    assert repository(engine).prepare(claim)["context"] is None


def test_legacy_kind_does_not_become_handoff_and_global_version_is_preserved(handoff):
    engine, case, conversation, send = handoff
    send(conversation())
    claim, _ = send(conversation(), finish=False)
    with engine.begin() as connection:
        connection.execute(
            insert(conversation_summaries).values(
                id="legacy",
                case_id=case,
                conversation_id=connection.scalar(
                    select(agent_runs.c.conversation_id).where(
                        agent_runs.c.id == claim.run_id
                    )
                ),
                snapshot_id=claim.snapshot_id,
                version=7,
                source_digest="legacy",
                summary_json={
                    "kind": "other",
                    "notes": [{"kind": "decision", "text": "bad"}],
                },
                created_at=_now(),
            )
        )
    view = repository(engine).prepare(claim)
    assert view["manifest"]["record_version"] == 8
    assert view["context"]["notes"] == []


def semantic_source(handoff):
    from backend.app.db.semantic_memory_repository import SemanticMemoryRepository

    engine, _, conversation, send = handoff
    conv = conversation()
    for number in range(12):
        send(conv, f"第{number}轮完整采购目标：" + "资料需要核对，不沿用旧报价。" * 20)
    old_claim, _ = send(conv, "整理此前目标及下一步", finish=False)
    semantic = SemanticMemoryRepository(engine)
    pending = semantic.prepare(old_claim)
    summary = semantic.save(
        old_claim,
        pending,
        [
            {
                "kind": "goal",
                "text": "核对文档",
                "source_message_ids": [pending["sources"][0]["message_id"]],
            },
            {
                "kind": "decision",
                "text": "旧结论999箱",
                "source_message_ids": [pending["sources"][1]["message_id"]],
            },
            {
                "kind": "next_step",
                "text": "历史待办：补资料",
                "source_message_ids": [pending["sources"][2]["message_id"]],
            },
        ],
    )
    RunRepository(engine).finish(
        old_claim.run_id,
        old_claim.active_attempt_id,
        old_claim.fence,
        old_claim.snapshot_id,
        RunStatus.COMPLETED,
        answer_text="仅历史答案",
        memory_json={"version": 5},
    )
    return summary, pending, conv


def test_valid_semantic_notes_exclude_decisions_and_pin_original_summary(handoff):
    engine, _, conversation, send = handoff
    summary, pending, _ = semantic_source(handoff)
    claim, _ = send(conversation(), finish=False)
    service = repository(engine)
    view = service.prepare(claim)
    assert {note["kind"] for note in view["context"]["notes"]} == {"goal", "next_step"}
    assert "旧结论999箱" not in str(view["context"])
    assert view["manifest"]["summary"]["id"] == summary["id"]
    assert service.read_history(
        claim, view, [pending["sources"][2]["message_id"]], part="question"
    )
    # A later B1 head must not replace the exact summary frozen by this Run.
    with engine.begin() as connection:
        row = dict(
            connection.execute(
                select(conversation_summaries).where(
                    conversation_summaries.c.id == summary["id"]
                )
            )
            .mappings()
            .one()
        )
        row.update(id="future-semantic", version=999, created_at=_now())
        connection.execute(insert(conversation_summaries).values(**row))
    assert service.prepare(claim) == view
    assert service.validate(claim, view) is None


@pytest.mark.parametrize("target", ["summary", "summary_source"])
def test_frozen_semantic_summary_and_omitted_decision_sources_are_revalidated(
    handoff, target
):
    engine, _, conversation, send = handoff
    summary, pending, _ = semantic_source(handoff)
    claim, _ = send(conversation(), finish=False)
    service = repository(engine)
    view = service.prepare(claim)
    with engine.begin() as connection:
        if target == "summary":
            payload = deepcopy(
                connection.scalar(
                    select(conversation_summaries.c.summary_json).where(
                        conversation_summaries.c.id == summary["id"]
                    )
                )
            )
            payload["notes"][0]["text"] = "篡改摘要"
            connection.execute(
                update(conversation_summaries)
                .where(conversation_summaries.c.id == summary["id"])
                .values(summary_json=payload)
            )
        else:
            connection.execute(
                update(messages)
                .where(messages.c.id == pending["sources"][1]["message_id"])
                .values(content="即使决策未注入也要验证B1原来源")
            )
    with pytest.raises(ValueError):
        service.validate(claim, view)


def test_same_message_multiple_phases_readback_retains_phase_identity(handoff):
    engine, _, conversation, send = handoff
    old_claim, old = send(conversation(), "同一问题有不同处理阶段")
    with engine.begin() as connection:
        row = dict(
            connection.execute(
                select(task_memory).where(
                    task_memory.c.source_run_id == old_claim.run_id
                )
            )
            .mappings()
            .one()
        )
        state = deepcopy(row["state_json"])
        state["answer_text"] = "新阶段答案" * 300
        state["status"] = "partial"
        row.update(
            id="new-phase",
            version=row["version"] + 1,
            state_json=state,
            memory_digest=digest(state),
            created_at=_now(),
        )
        connection.execute(insert(task_memory).values(**row))
    claim, _ = send(conversation(), finish=False)
    service = repository(engine)
    view = service.prepare(claim)
    assert len(view["manifest"]["sources"]) == 2
    pages = service.read_history(claim, view, [old["message_id"]], part="answer")
    assert len(pages) == 2 and len({page["task_memory_id"] for page in pages}) == 2
    long_page = next(page for page in pages if page["next_char"] is not None)
    later = service.read_history(
        claim, view, [old["message_id"]], part="both", start_char=long_page["next_char"]
    )
    assert len(later) == 1 and later[0]["task_memory_id"] == "new-phase"


def test_sources_from_a_different_case_are_not_captured(handoff, ingestion_case):
    engine, _, conversation, send = handoff
    client = ingestion_case[0]
    other_case = client.post("/api/v1/cases").json()["id"]
    other_conversation = client.post(
        f"/api/v1/cases/{other_case}/conversations"
    ).json()["id"]
    send(other_conversation, "其他采购任务的机密问题")
    claim, _ = send(conversation(), finish=False)
    assert repository(engine).prepare(claim)["context"] is None


def test_explicit_retry_keeps_original_capture_while_newer_conversation_exists(handoff):
    engine, _, conversation, send = handoff
    send(conversation(), "原始目标")
    claim, _ = send(conversation(), finish=False)
    service = repository(engine)
    view = service.prepare(claim)
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
    send(conversation(), "未来新目标不应混入重试")
    receipt = runs.retry(claim.run_id, claim.snapshot_id, 0, "retry-task-handoff")
    retry = runs.claim(receipt.run_id, receipt.attempt_id)
    assert service.prepare(retry) == view
    assert service.validate(retry, view) is None


@pytest.mark.parametrize("length", [600, 650, 700, 720])
def test_context_budget_includes_serialized_scope_and_full_optional_units(
    handoff, length
):
    import json

    engine, _, conversation, send = handoff
    original = "字" * length
    send(conversation(), original)
    claim, _ = send(conversation(), finish=False)
    context = repository(engine).prepare(claim)["context"]
    assert len(json.dumps(context, ensure_ascii=False).encode()) <= 3500
    for field in ("goal", "recent_request"):
        assert "text" not in context[field] or context[field]["text"] == original


def test_large_valid_semantic_manifest_keeps_whole_notes_with_bounded_sources(handoff):
    from backend.app.db.semantic_memory_repository import SemanticMemoryRepository

    engine, _, conversation, send = handoff
    old = conversation()
    for number in range(20):
        send(old, f"第{number}轮" + "原始需求完整保存。" * 30)
    old_claim, _ = send(old, "最近请求。" * 100, finish=False)
    semantic = SemanticMemoryRepository(engine)
    for _ in range(3):
        pending = semantic.prepare(old_claim)
        refs = [source["message_id"] for source in pending["sources"]]
        semantic.save(
            old_claim,
            pending,
            [
                {
                    "kind": "next_step",
                    "text": f"历史步骤{index}" + "待核对" * 25,
                    "source_message_ids": refs[index : index + 2],
                }
                for index in range(0, len(refs), 2)
            ],
        )
    RunRepository(engine).finish(
        old_claim.run_id,
        old_claim.active_attempt_id,
        old_claim.fence,
        old_claim.snapshot_id,
        RunStatus.COMPLETED,
        answer_text="历史答复",
        memory_json={"version": 5},
    )
    claim, _ = send(conversation(), finish=False)
    view = repository(engine).prepare(claim)
    assert view["context"]["notes"]
    assert len(view["manifest"]["sources"]) <= 10
    allowed = {source["message_id"] for source in view["manifest"]["sources"]}
    assert all(
        set(note["source_message_ids"]) <= allowed for note in view["context"]["notes"]
    )
    required = {
        view["context"][field]["task_memory_id"] for field in ("goal", "recent_request")
    }
    note_refs = {
        ref for note in view["context"]["notes"] for ref in note["source_message_ids"]
    }
    assert all(
        source["task_memory_id"] in required or source["message_id"] in note_refs
        for source in view["manifest"]["sources"]
    )


def set_queued_cutoff(engine, run_id, value, *, first=True):
    with engine.begin() as connection:
        event = (
            connection.execute(
                select(run_events)
                .where(run_events.c.run_id == run_id, run_events.c.kind == "queued")
                .order_by(run_events.c.seq.asc() if first else run_events.c.seq.desc())
                .limit(1)
            )
            .mappings()
            .one()
        )
        summary = dict(event["summary_json"])
        summary["task_handoff_cutoff_at"] = value
        connection.execute(
            update(run_events)
            .where(run_events.c.id == event["id"])
            .values(summary_json=summary)
        )


def test_mysql_seconds_precision_simulated_with_sqlite_uses_precise_queued_cutoff(
    handoff,
):
    """SQLite timestamp edits simulate DATETIME(0); this is not a MySQL run."""
    engine, _, conversation, send = handoff
    old_claim, old = send(conversation(), "同秒内已经完成的采购目标")
    claim, receipt = send(conversation(), finish=False)
    future_claim, _ = send(conversation(), "同秒稍晚完成的未来内容")
    second = _now().replace(microsecond=0) - timedelta(seconds=1)
    with engine.begin() as connection:
        connection.execute(update(messages).values(created_at=second))
        connection.execute(update(agent_runs).values(created_at=second))
        connection.execute(
            update(task_memory)
            .where(task_memory.c.source_run_id == old_claim.run_id)
            .values(created_at=second + timedelta(microseconds=100))
        )
        connection.execute(
            update(task_memory)
            .where(task_memory.c.source_run_id == future_claim.run_id)
            .values(created_at=second + timedelta(microseconds=900))
        )
    set_queued_cutoff(
        engine,
        claim.run_id,
        (second + timedelta(microseconds=500)).isoformat(timespec="microseconds"),
    )
    view = repository(engine).prepare(claim)
    assert view["context"]["recent_request"]["message_id"] == old["message_id"]
    assert receipt["message_id"] not in {
        source["message_id"] for source in view["manifest"]["sources"]
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        23,
        {},
        "wrong",
        "2026-10-22T01:00:00Z",
        "2026-10-22T01:00:00.000000+00:00",
        "2026-99-22T01:00:00.000000",
        "2000-01-01T00:00:00.000000",
    ],
)
def test_malformed_or_far_before_root_precise_cutoff_fails_closed(handoff, value):
    engine, _, conversation, send = handoff
    claim, _ = send(conversation(), finish=False)
    set_queued_cutoff(engine, claim.run_id, value)
    with pytest.raises(ValueError):
        repository(engine).prepare(claim)


def test_frozen_precise_cutoff_is_checked_even_when_source_selection_would_not_change(
    handoff,
):
    engine, _, conversation, send = handoff
    send(conversation())
    claim, _ = send(conversation(), finish=False)
    cutoff = _now()
    set_queued_cutoff(engine, claim.run_id, cutoff.isoformat(timespec="microseconds"))
    service = repository(engine)
    view = service.prepare(claim)
    set_queued_cutoff(
        engine,
        claim.run_id,
        (cutoff + timedelta(microseconds=1)).isoformat(timespec="microseconds"),
    )
    with pytest.raises(ValueError):
        service.validate(claim, view)
    with pytest.raises(ValueError):
        service.prepare(claim)


@pytest.mark.parametrize("legacy", [False, True])
def test_retry_reads_first_queued_cutoff_and_legacy_without_marker_keeps_strict_fallback(
    handoff, legacy
):
    engine, _, conversation, send = handoff
    claim, receipt = send(conversation(), finish=False)
    service = repository(engine)
    # With no exact marker, a phase in the same stored second is conservatively
    # excluded rather than broadening the historical window by one second.
    old_claim, _ = send(conversation())
    with engine.begin() as connection:
        root = connection.scalar(
            select(messages.c.created_at).where(messages.c.id == receipt["message_id"])
        )
        event = (
            connection.execute(
                select(run_events)
                .where(
                    run_events.c.run_id == claim.run_id, run_events.c.kind == "queued"
                )
                .order_by(run_events.c.seq)
                .limit(1)
            )
            .mappings()
            .one()
        )
        boundary = (
            root
            if legacy
            else datetime.fromisoformat(event["summary_json"]["task_handoff_cutoff_at"])
        )
        connection.execute(
            update(task_memory)
            .where(task_memory.c.source_run_id == old_claim.run_id)
            .values(created_at=boundary)
        )
        if legacy:
            summary = dict(event["summary_json"])
            summary.pop("task_handoff_cutoff_at", None)
            connection.execute(
                update(run_events)
                .where(run_events.c.id == event["id"])
                .values(summary_json=summary)
            )
    view = service.prepare(claim)
    assert view["context"] is None
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
    retry_receipt = runs.retry(claim.run_id, claim.snapshot_id, 0, "retry-cutoff")
    retry = runs.claim(retry_receipt.run_id, retry_receipt.attempt_id)
    set_queued_cutoff(engine, claim.run_id, "wrong-retry-marker", first=False)
    assert service.prepare(retry) == view


@pytest.mark.parametrize("rounded", ["current_root", "source_root", "both"])
def test_mysql_rounding_up_simulated_in_sqlite_keeps_earlier_precise_phase(
    handoff, rounded
):
    """MySQL DATETIME(0) rounding is simulated, not tested against MySQL."""
    engine, _, conversation, send = handoff
    old_claim, old = send(conversation(), "舍入前已完成的来源")
    claim, receipt = send(conversation(), finish=False)
    second = _now().replace(hour=12, minute=0, second=0, microsecond=0)
    cutoff = second + timedelta(microseconds=800100)
    rounded_up = second + timedelta(seconds=1)
    with engine.begin() as connection:
        connection.execute(
            update(task_memory)
            .where(task_memory.c.source_run_id == old_claim.run_id)
            .values(created_at=second + timedelta(microseconds=700000))
        )
        current_time = rounded_up if rounded in {"current_root", "both"} else second
        source_time = rounded_up if rounded in {"source_root", "both"} else second
        connection.execute(
            update(messages)
            .where(messages.c.id == receipt["message_id"])
            .values(created_at=current_time)
        )
        connection.execute(
            update(messages)
            .where(messages.c.id == old["message_id"])
            .values(created_at=source_time)
        )
        connection.execute(
            update(agent_runs)
            .where(agent_runs.c.id == old_claim.run_id)
            .values(created_at=source_time)
        )
    set_queued_cutoff(engine, claim.run_id, cutoff.isoformat(timespec="microseconds"))
    service = repository(engine)
    view = service.prepare(claim)
    assert view["context"]["recent_request"]["message_id"] == old["message_id"]
    assert service.validate(claim, view) is None
    assert (
        service.read_history(claim, view, [old["message_id"]], part="question")[0][
            "text"
        ]
        == "舍入前已完成的来源"
    )


def test_mysql_legacy_rounded_root_simulated_in_sqlite_uses_safe_lower_bound(handoff):
    engine, _, conversation, send = handoff
    old_claim, _ = send(conversation(), "确实较早完成的旧来源")
    claim, receipt = send(conversation(), finish=False)
    future_claim, _ = send(conversation(), "实际在提问后完成但早于舍入秒的未来来源")
    second = _now().replace(hour=12, minute=0, second=0, microsecond=0)
    with engine.begin() as connection:
        connection.execute(update(messages).values(created_at=second))
        connection.execute(update(agent_runs).values(created_at=second))
        connection.execute(
            update(messages)
            .where(messages.c.id == receipt["message_id"])
            .values(created_at=second + timedelta(seconds=1))
        )
        connection.execute(
            update(task_memory)
            .where(task_memory.c.source_run_id == old_claim.run_id)
            .values(created_at=second - timedelta(microseconds=100000))
        )
        connection.execute(
            update(task_memory)
            .where(task_memory.c.source_run_id == future_claim.run_id)
            .values(created_at=second + timedelta(microseconds=900000))
        )
        event = (
            connection.execute(
                select(run_events)
                .where(
                    run_events.c.run_id == claim.run_id, run_events.c.kind == "queued"
                )
                .order_by(run_events.c.seq)
                .limit(1)
            )
            .mappings()
            .one()
        )
        summary = dict(event["summary_json"])
        summary.pop("task_handoff_cutoff_at", None)
        connection.execute(
            update(run_events)
            .where(run_events.c.id == event["id"])
            .values(summary_json=summary)
        )
    view = repository(engine).prepare(claim)
    assert view["context"]["recent_request"]["text"] == "确实较早完成的旧来源"
    assert view["manifest"]["cutoff_at"] == second.isoformat(timespec="microseconds")
