"""Immutable terminal responses paired with SQL original user messages."""

from sqlalchemy import Connection, func, or_, select

from ..evidence.repository import digest
from .models import agent_runs, messages, run_attempts, task_memory


def checkpoint_state(connection: Connection, run: dict) -> dict:
    reply = connection.scalar(
        select(run_attempts.c.resume_message_id).where(
            run_attempts.c.id == run["active_attempt_id"],
            run_attempts.c.run_id == run["id"],
        )
    )
    if reply is None:
        reply = connection.scalar(
            select(run_attempts.c.resume_message_id)
            .where(
                run_attempts.c.run_id == run["id"],
                run_attempts.c.resume_message_id.is_not(None),
            )
            .order_by(run_attempts.c.attempt_no.desc())
            .limit(1)
        )
    message_id = reply or run["message_id"]
    question = connection.scalar(
        select(messages.c.content).where(
            messages.c.id == message_id,
            messages.c.conversation_id == run["conversation_id"],
        )
    )
    memory = run["memory_json"]
    return {
        "focus": memory.get("focus"),
        "pending_clarification": memory.get("pending_clarification"),
        "checkpoint": memory,
        "source_message_id": message_id,
        "source_attempt_id": run["active_attempt_id"],
        "question_digest": digest(question),
        "answer_text": run["answer_text"] or "",
        "status": run["status"],
    }


def load_turns(
    connection: Connection,
    case_id: str,
    conversation_id: str,
    snapshot_id: str,
    *,
    limit: int = 28,
    before=None,
    own_run_id: str | None = None,
    exclude_run_id: str | None = None,
    message_ids: list[str] | None = None,
    task_ids: list[str] | None = None,
    before_sequence: int | None = None,
) -> list[dict]:
    prior = task_memory.alias("latest_checkpoint")
    latest_version = (
        select(func.max(prior.c.version))
        .where(
            prior.c.conversation_id == task_memory.c.conversation_id,
            prior.c.source_run_id == task_memory.c.source_run_id,
        )
        .correlate(task_memory)
        .scalar_subquery()
    )
    query = (
        select(
            task_memory,
            latest_version.label("latest_version"),
            agent_runs.c.message_id.label("original_message_id"),
            agent_runs.c.memory_json.label("current_memory"),
            agent_runs.c.status.label("current_status"),
            agent_runs.c.answer_text.label("current_answer"),
            agent_runs.c.active_attempt_id.label("current_attempt_id"),
        )
        .join(agent_runs, agent_runs.c.id == task_memory.c.source_run_id)
        .where(
            task_memory.c.case_id == case_id,
            task_memory.c.conversation_id == conversation_id,
            task_memory.c.snapshot_id == snapshot_id,
        )
    )
    if before_sequence is not None:
        root = messages.alias("run_initial_message")
        sequence = (
            select(root.c.sequence)
            .where(root.c.id == agent_runs.c.message_id)
            .correlate(agent_runs)
            .scalar_subquery()
        )
        query = query.where(
            or_(sequence < before_sequence, task_memory.c.source_run_id == own_run_id)
        )
    elif before is not None:
        query = query.where(
            or_(
                task_memory.c.created_at <= before,
                task_memory.c.source_run_id == own_run_id,
            )
        )
    if exclude_run_id:
        query = query.where(task_memory.c.source_run_id != exclude_run_id)
    if message_ids is not None:
        query = query.where(
            task_memory.c.state_json["source_message_id"].as_string().in_(message_ids)
        )
    if task_ids is not None:
        query = query.where(task_memory.c.id.in_(task_ids))
    rows = (
        connection.execute(query.order_by(task_memory.c.version.desc()).limit(limit))
        .mappings()
        .all()
    )
    if not rows:
        return []
    states = [row["state_json"] for row in rows]
    if any(
        not isinstance(state, dict)
        or not isinstance(state.get("source_message_id"), str)
        or not isinstance(state.get("checkpoint"), dict)
        or not isinstance(state.get("answer_text"), str)
        or state.get("status") not in {"completed", "partial", "waiting_user"}
        for state in states
    ):
        raise ValueError("memory_source_changed")
    originals = (
        connection.execute(
            select(messages).where(
                messages.c.conversation_id == conversation_id,
                messages.c.id.in_([state["source_message_id"] for state in states]),
            )
        )
        .mappings()
        .all()
    )
    by_id = {row["id"]: row for row in originals}
    replies = set(
        connection.execute(
            select(run_attempts.c.run_id, run_attempts.c.resume_message_id).where(
                run_attempts.c.run_id.in_([row["source_run_id"] for row in rows]),
                run_attempts.c.resume_message_id.in_(list(by_id)),
            )
        ).all()
    )
    result = []
    for row, state in zip(rows, states, strict=True):
        message = by_id.get(state["source_message_id"])
        if (
            message is None
            or message["role"] != "user"
            or digest(state) != row["memory_digest"]
            or digest(message["content"]) != state["question_digest"]
            or (
                message["id"] != row["original_message_id"]
                and (row["source_run_id"], message["id"]) not in replies
            )
            or state["focus"] != state["checkpoint"].get("focus")
            or state["pending_clarification"]
            != state["checkpoint"].get("pending_clarification")
        ):
            raise ValueError("memory_source_changed")
        if (
            row["current_attempt_id"] == state["source_attempt_id"]
            and row["version"] == row["latest_version"]
            and row["current_status"] == state["status"]
            and (
                digest(row["current_memory"]) != digest(state["checkpoint"])
                or (row["current_answer"] or "") != state["answer_text"]
            )
        ):
            raise ValueError("memory_source_changed")
        result.append(
            {
                "id": row["source_run_id"],
                "task_memory_id": row["id"],
                "version": row["version"],
                "message_id": message["id"],
                "sequence": message["sequence"],
                "content": message["content"],
                "answer_text": state["answer_text"],
                "state": state,
                "created_at": row["created_at"],
            }
        )
    return result
