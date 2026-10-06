"""Read SQL user messages and immutable response phases with one temporal scope."""

from dataclasses import dataclass, field
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import Engine, select

from .conversation_turns import load_turns
from .models import agent_runs, messages, run_attempts


class ConversationContextError(ValueError):
    pass


@dataclass(frozen=True)
class ConversationContext:
    question: str
    memory: dict | None
    history: list[tuple[HumanMessage, AIMessage]]
    message_ids: list[str] = field(default_factory=list)
    current_message_id: str | None = None
    resumed: bool = False
    cutoff: datetime | None = None
    cutoff_sequence: int | None = None
    own_checkpoint_id: str | None = None


def load_conversation_context(
    engine: Engine, run_id: str, attempt_id: str
) -> ConversationContext:
    with engine.connect() as connection:
        run = (
            connection.execute(
                select(agent_runs).where(
                    agent_runs.c.id == run_id,
                    agent_runs.c.active_attempt_id == attempt_id,
                )
            )
            .mappings()
            .one()
        )
        attempt = (
            connection.execute(
                select(run_attempts).where(
                    run_attempts.c.id == attempt_id, run_attempts.c.run_id == run_id
                )
            )
            .mappings()
            .one()
        )
        cutoff_sequence = connection.scalar(
            select(messages.c.sequence).where(messages.c.id == run["message_id"])
        )
        reply_id = attempt["resume_message_id"]
        if reply_id is None:
            # A retry of an interrupted clarification continues its latest user
            # reply, rather than silently returning to the original question.
            reply_id = connection.scalar(
                select(run_attempts.c.resume_message_id)
                .where(
                    run_attempts.c.run_id == run_id,
                    run_attempts.c.attempt_no < attempt["attempt_no"],
                    run_attempts.c.resume_message_id.is_not(None),
                )
                .order_by(run_attempts.c.attempt_no.desc())
                .limit(1)
            )
        current_message_id = reply_id or run["message_id"]
        question = connection.scalar(
            select(messages.c.content).where(
                messages.c.id == current_message_id,
                messages.c.conversation_id == run["conversation_id"],
                messages.c.role == "user",
            )
        )
        if not question:
            raise ConversationContextError("memory_scope")
        try:
            phases = load_turns(
                connection,
                run["case_id"],
                run["conversation_id"],
                run["snapshot_id"],
                limit=8,
                before=run["created_at"],
                own_run_id=run_id,
                before_sequence=cutoff_sequence,
            )
        except ValueError:
            raise ConversationContextError("memory_scope") from None
        legacy = (
            connection.execute(
                select(
                    agent_runs.c.id,
                    agent_runs.c.message_id,
                    agent_runs.c.answer_text,
                    agent_runs.c.memory_json,
                    agent_runs.c.created_at,
                    messages.c.content,
                )
                .join(
                    messages,
                    (messages.c.id == agent_runs.c.message_id)
                    & (messages.c.conversation_id == agent_runs.c.conversation_id),
                )
                .where(
                    agent_runs.c.case_id == run["case_id"],
                    agent_runs.c.conversation_id == run["conversation_id"],
                    agent_runs.c.snapshot_id == run["snapshot_id"],
                    agent_runs.c.id != run_id,
                    agent_runs.c.status == "completed",
                    messages.c.sequence < cutoff_sequence
                    if cutoff_sequence is not None
                    else agent_runs.c.updated_at <= run["created_at"],
                )
                .order_by(agent_runs.c.created_at.desc(), agent_runs.c.id.desc())
                .limit(8)
            )
            .mappings()
            .all()
        )
        # Old pre-0013 Runs remain readable. Their immutable phase replacements
        # take precedence; no duplicate question/final-answer cache is injected.
        phase_run_ids = {item["id"] for item in phases}
        turns = [
            dict(item)
            for item in legacy
            if item["id"] not in phase_run_ids and item["answer_text"]
        ]
        turns.extend(phases)
        turns.sort(
            key=lambda item: (item["created_at"], item.get("version", 0), item["id"])
        )
        turns = turns[-8:]
        history = [
            (
                HumanMessage(content=item["content"], id=item["message_id"]),
                AIMessage(content=item["answer_text"]),
            )
            for item in turns
        ]
        memory = next(
            (
                item["state"]["checkpoint"] if "state" in item else item["memory_json"]
                for item in reversed(turns)
                if "state" in item or item["memory_json"] is not None
            ),
            None,
        )
        if memory is None:
            memory = next(
                (
                    item["memory_json"]
                    for item in legacy
                    if item["memory_json"] is not None
                ),
                None,
            )
        own_phase = next((item for item in phases if item["id"] == run_id), None)
        if own_phase is not None:
            memory = own_phase["state"]["checkpoint"]
        if reply_id is not None and not any(item["id"] == run_id for item in phases):
            memory = run["memory_json"]
            if not isinstance(memory, dict) or not memory.get("pending_clarification"):
                raise ConversationContextError("memory_scope")
            # Legacy resume has no immutable response phase; preserve its last
            # already validated serialized dialogue until the next checkpoint.
            cached = memory.get("recent_turns") or []
            if (
                not isinstance(cached, list)
                or len(cached) > 8
                or any(
                    not isinstance(t, dict)
                    or not isinstance(t.get("question"), str)
                    or not isinstance(t.get("answer"), str)
                    for t in cached
                )
            ):
                raise ConversationContextError("memory_scope")
            original = connection.scalar(
                select(messages.c.content).where(messages.c.id == run["message_id"])
            )
            prior_replies = connection.execute(
                select(messages.c.id, messages.c.content)
                .join(run_attempts, run_attempts.c.resume_message_id == messages.c.id)
                .where(
                    run_attempts.c.run_id == run_id,
                    run_attempts.c.attempt_no < attempt["attempt_no"],
                    messages.c.conversation_id == run["conversation_id"],
                )
                .order_by(run_attempts.c.attempt_no.desc())
                .limit(8)
            ).all()
            known = {item["content"]: item["message_id"] for item in legacy}
            known[original] = run["message_id"]
            known.update({item.content: item.id for item in reversed(prior_replies)})
            if any(t["question"] not in known for t in cached):
                raise ConversationContextError("memory_scope")
            history = [
                (
                    HumanMessage(content=t["question"], id=known[t["question"]]),
                    AIMessage(content=t["answer"]),
                )
                for t in cached
            ]
        return ConversationContext(
            question,
            memory,
            history,
            [q.id for q, _ in history if q.id],
            current_message_id,
            reply_id is not None,
            run["created_at"],
            cutoff_sequence,
            own_phase["task_memory_id"] if own_phase else None,
        )
