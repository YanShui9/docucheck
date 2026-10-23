"""Original-message lookup uses the current Case, never model-supplied IDs."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, insert, select, update

from backend.app.db.models import (
    agent_runs,
    assistant_runs,
    cases,
    conversation_summaries,
    conversations,
    input_snapshots,
    messages,
    project_members,
    projects,
    run_attempts,
    run_events,
)
from backend.app.db.run_repository import _now
from backend.tests import test_task_handoff_repository

handoff = test_task_handoff_repository.handoff
ingestion_case = test_task_handoff_repository.ingestion_case


def store(engine):
    from backend.app.db.first_task_message_repository import FirstTaskMessageRepository

    return FirstTaskMessageRepository(engine)


def test_cross_conversation_returns_original_and_readable_source(handoff):
    engine, _, conversation, send = handoff
    old = conversation()
    _, receipt = send(old, "我想买苹果20箱。然后每箱的单价不得超过25块")
    send(conversation(), "后来的请求")
    claim, _ = send(conversation(), "最早说了什么", finish=False)
    service = store(engine)
    view = service.prepare(claim)
    page = service.read(claim, view)
    assert page["text"] == "我想买苹果20箱。然后每箱的单价不得超过25块"
    assert page["conversation_number"] == 1
    assert page["revision"] == 0
    assert page["next_char"] is None
    assert view["source"]["message_id"] == receipt["message_id"]
    assert "苹果" not in str(view)
    assert service.prepare(claim) == view
    service.validate(claim, view)


def test_current_message_is_not_history_and_empty_result_is_frozen(handoff):
    engine, _, conversation, send = handoff
    claim, _ = send(conversation(), "最早说了什么", finish=False)
    service = store(engine)
    view = service.prepare(claim)
    assert service.read(claim, view) is None
    send(conversation(), "后来的消息不能追入旧查询")
    assert service.prepare(claim) == view
    assert service.read(claim, view) is None


def test_changed_original_is_rejected(handoff):
    engine, _, conversation, send = handoff
    _, first = send(conversation(), "原话")
    claim, _ = send(conversation(), finish=False)
    service = store(engine)
    view = service.prepare(claim)
    with engine.begin() as connection:
        connection.execute(update(messages).where(messages.c.id == first["message_id"]).values(content="被修改"))
    with pytest.raises(ValueError):
        service.read(claim, view)


def test_long_original_is_paged_without_losing_characters(handoff):
    engine, _, conversation, send = handoff
    original = "中文带标点。" * 250
    send(conversation(), original)
    claim, _ = send(conversation(), finish=False)
    service = store(engine)
    view = service.prepare(claim)
    position, pages = 0, []
    while True:
        page = service.read(claim, view, start_char=position)
        assert len(page["text"].encode("utf-8")) <= 1600
        assert page["total_chars"] == len(original)
        pages.append(page["text"])
        if page["next_char"] is None:
            break
        assert page["next_char"] > position
        position = page["next_char"]
    assert "".join(pages) == original
    with pytest.raises(ValueError):
        service.read(claim, view, start_char=len(original))


def test_stale_fence_and_cancel_cannot_read(handoff):
    engine, _, conversation, send = handoff
    send(conversation(), "私有原话")
    claim, _ = send(conversation(), finish=False)
    service = store(engine)
    view = service.prepare(claim)
    with pytest.raises(ValueError):
        service.read(replace(claim, fence=claim.fence + 1), view)
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == claim.run_id).values(status="cancelled"))
    with pytest.raises(ValueError):
        service.read(claim, view)


def test_frozen_manifest_has_no_original_text(handoff):
    engine, _, conversation, send = handoff
    send(conversation(), "绝不进入manifest的原话")
    claim, _ = send(conversation(), finish=False)
    service = store(engine)
    view = service.prepare(claim)
    with engine.connect() as connection:
        payloads = connection.scalars(select(conversation_summaries.c.summary_json)).all()
    assert len(payloads) == 1
    assert "绝不进入manifest的原话" not in str(payloads)
    bad = dict(view) | {"input_digest": "0" * 64}
    with pytest.raises(ValueError):
        service.read(claim, bad)


def test_same_second_uses_precise_submission_before_id_tie_break(handoff):
    engine, _, conversation, send = handoff
    old1, first = send(conversation(), "第一条")
    old2, second = send(conversation(), "第二条")
    claim, _ = send(conversation(), finish=False)
    base = datetime(2026, 10, 23, 12, tzinfo=UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        for run, offset in [(old1, 100000), (old2, 200000), (claim, 800000)]:
            connection.execute(update(run_events).where(run_events.c.run_id == run.run_id, run_events.c.kind == "queued").values(
                summary_json={"task_handoff_cutoff_at": (base + timedelta(microseconds=offset)).isoformat(timespec="microseconds")}))
            message_id = connection.scalar(select(agent_runs.c.message_id).where(agent_runs.c.id == run.run_id))
            connection.execute(update(messages).where(messages.c.id == message_id).values(created_at=base))
        # Force the later message's conversation to sort first without precise time.
        connection.execute(update(messages).where(messages.c.id == first["message_id"]).values(sequence=2))
        connection.execute(update(messages).where(messages.c.id == second["message_id"]).values(sequence=1))
        first_conv = connection.scalar(select(messages.c.conversation_id).where(messages.c.id == first["message_id"]))
        second_conv = connection.scalar(select(messages.c.conversation_id).where(messages.c.id == second["message_id"]))
        connection.execute(update(conversations).where(conversations.c.id == first_conv).values(created_at=base + timedelta(seconds=1)))
        connection.execute(update(conversations).where(conversations.c.id == second_conv).values(created_at=base))
    service = store(engine)
    assert service.read(claim, service.prepare(claim))["text"] == "第一条"


def test_precise_future_message_is_excluded_even_if_stored_time_is_earlier(handoff):
    engine, _, conversation, send = handoff
    future, _ = send(conversation(), "不能追入的稍晚消息")
    claim, _ = send(conversation(), finish=False)
    base = datetime(2026, 10, 23, 12, tzinfo=UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        for run, offset in [(future, 900000), (claim, 800000)]:
            connection.execute(update(run_events).where(run_events.c.run_id == run.run_id, run_events.c.kind == "queued").values(
                summary_json={"task_handoff_cutoff_at": (base + timedelta(microseconds=offset)).isoformat(timespec="microseconds")}))
            msg = connection.scalar(select(agent_runs.c.message_id).where(agent_runs.c.id == run.run_id))
            connection.execute(update(messages).where(messages.c.id == msg).values(created_at=base))
    service = store(engine)
    assert service.read(claim, service.prepare(claim)) is None


def test_old_snapshot_can_be_read_without_widening_current_source_scope(handoff):
    engine, case, conversation, send = handoff
    send(conversation(), "资料版本0的最早原话")
    claim, _ = send(conversation(), finish=False)
    with engine.begin() as connection:
        snapshot = dict(connection.execute(select(input_snapshots).where(input_snapshots.c.id == claim.snapshot_id)).mappings().one())
        connection.execute(insert(input_snapshots).values(**(snapshot | {"id": "new-snapshot", "revision": 1})))
        connection.execute(update(cases).where(cases.c.id == case).values(revision=1))
        connection.execute(update(agent_runs).where(agent_runs.c.id == claim.run_id).values(snapshot_id="new-snapshot", revision=1))
        from backend.app.db.item_binding_repository import freeze_current

        frozen = freeze_current(connection, case, "new-snapshot", None, None)
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == claim.run_id).values(
            snapshot_id="new-snapshot", item_binding_digest=frozen["digest"]))
    claim = replace(claim, snapshot_id="new-snapshot")
    service = store(engine)
    page = service.read(claim, service.prepare(claim))
    assert page["text"] == "资料版本0的最早原话" and page["revision"] == 0


@pytest.mark.parametrize("target", ["member", "delete", "attempt", "revision", "source_time", "cutoff", "source_cutoff"])
def test_authority_and_frozen_source_guards(handoff, target):
    engine, case, conversation, send = handoff
    old, _ = send(conversation(), "受保护的原话")
    claim, _ = send(conversation(), finish=False)
    service = store(engine)
    view = service.prepare(claim)
    with engine.begin() as connection:
        if target == "member":
            connection.execute(insert(projects).values(id="revoked-project", name="revoked", created_at=_now()))
            connection.execute(update(cases).where(cases.c.id == case).values(project_id="revoked-project"))
            connection.execute(delete(project_members))
        elif target == "delete":
            connection.execute(update(cases).where(cases.c.id == case).values(deletion_requested_at=_now()))
        elif target == "attempt":
            connection.execute(update(run_attempts).where(run_attempts.c.id == claim.active_attempt_id).values(status="interrupted"))
        elif target == "revision":
            connection.execute(update(cases).where(cases.c.id == case).values(revision=1))
        elif target == "source_time":
            message_id = connection.scalar(select(agent_runs.c.message_id).where(agent_runs.c.id == old.run_id))
            connection.execute(update(messages).where(messages.c.id == message_id).values(created_at=_now()))
        else:
            run_id = old.run_id if target == "source_cutoff" else claim.run_id
            connection.execute(update(run_events).where(run_events.c.run_id == run_id, run_events.c.kind == "queued").values(
                summary_json={"task_handoff_cutoff_at": "invalid"}))
    for action in [lambda: service.prepare(claim), lambda: service.validate(claim, view), lambda: service.read(claim, view)]:
        with pytest.raises(ValueError):
            action()


@pytest.mark.parametrize("seconds_before,found", [(0, False), (1, True), (2, True)])
def test_legacy_source_without_precise_time_uses_safe_full_second_boundary(handoff, seconds_before, found):
    engine, _, conversation, send = handoff
    old, receipt = send(conversation(), "旧系统的原话")
    claim, _ = send(conversation(), finish=False)
    base = datetime(2026, 10, 23, 12, tzinfo=UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(update(run_events).where(run_events.c.run_id == old.run_id, run_events.c.kind == "queued").values(summary_json={}))
        connection.execute(update(messages).where(messages.c.id == receipt["message_id"]).values(created_at=base - timedelta(seconds=seconds_before)))
        root = connection.scalar(select(agent_runs.c.message_id).where(agent_runs.c.id == claim.run_id))
        connection.execute(update(messages).where(messages.c.id == root).values(created_at=base))
        connection.execute(update(run_events).where(run_events.c.run_id == claim.run_id, run_events.c.kind == "queued").values(
            summary_json={"task_handoff_cutoff_at": (base + timedelta(microseconds=800000)).isoformat(timespec="microseconds")}))
    service = store(engine)
    page = service.read(claim, service.prepare(claim))
    assert (page is not None) == found


def test_failed_old_run_without_checkpoint_still_preserves_user_original(handoff):
    engine, _, conversation, send = handoff
    old, _ = send(conversation(), "失败处理之前提交的原话", finish=False)
    from backend.app.db.run_repository import RunRepository

    RunRepository(engine).interrupt(old.run_id, old.active_attempt_id, old.fence)
    claim, _ = send(conversation(), finish=False)
    service = store(engine)
    assert service.read(claim, service.prepare(claim))["text"] == "失败处理之前提交的原话"


def test_explicit_retry_reuses_first_lookup_record(handoff):
    engine, _, conversation, send = handoff
    send(conversation(), "已冻结的最早原话")
    claim, _ = send(conversation(), finish=False)
    service = store(engine)
    view = service.prepare(claim)
    from backend.app.db.run_repository import RunRepository
    from backend.app.runtime.lifecycle import RunStatus

    runs = RunRepository(engine)
    runs.finish(claim.run_id, claim.active_attempt_id, claim.fence, claim.snapshot_id,
                RunStatus.PARTIAL, answer_text="尚未完成。", error_code="deadline")
    send(conversation(), "提交后的消息")
    retried = runs.retry(claim.run_id, claim.snapshot_id, 0, "lookup-retry")
    new_claim = runs.claim(claim.run_id, retried.attempt_id)
    assert service.prepare(new_claim) == view
    assert service.read(new_claim, view)["text"] == "已冻结的最早原话"
