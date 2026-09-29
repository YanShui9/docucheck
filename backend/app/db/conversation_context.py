"""Read a Run's bounded conversation context from its immutable snapshot."""

from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import Engine, select

from .models import agent_runs, messages, run_attempts


class ConversationContextError(ValueError):
    pass


@dataclass(frozen=True)
class ConversationContext:
    question: str
    memory: dict | None
    history: list[tuple[HumanMessage, AIMessage]]


def load_conversation_context(engine: Engine, run_id: str,
                              attempt_id: str) -> ConversationContext:
    with engine.connect() as connection:
        run = connection.execute(select(agent_runs).where(
            agent_runs.c.id == run_id,
            agent_runs.c.active_attempt_id == attempt_id,
        )).mappings().one()
        original = connection.scalar(select(messages.c.content).where(
            messages.c.id == run["message_id"],
            messages.c.conversation_id == run["conversation_id"],
        ))
        reply_id = connection.scalar(select(run_attempts.c.resume_message_id).where(
            run_attempts.c.id == attempt_id,
            run_attempts.c.run_id == run_id,
        ))
        previous = connection.execute(select(
            agent_runs.c.message_id,
            agent_runs.c.answer_text,
            agent_runs.c.memory_json,
        ).where(
            agent_runs.c.conversation_id == run["conversation_id"],
            agent_runs.c.snapshot_id == run["snapshot_id"],
            agent_runs.c.id != run_id,
            agent_runs.c.status == "completed",
            agent_runs.c.created_at <= run["created_at"],
        ).order_by(agent_runs.c.created_at.desc(), agent_runs.c.id.desc())
            .limit(4)).mappings().all()
        history = []
        for item in reversed(previous):
            question = connection.scalar(select(messages.c.content).where(
                messages.c.id == item["message_id"],
                messages.c.conversation_id == run["conversation_id"],
            ))
            if question and item["answer_text"]:
                history.append((HumanMessage(content=question),
                                AIMessage(content=item["answer_text"])))
        memory = next((item["memory_json"] for item in previous
                       if item["memory_json"] is not None), None)
        if reply_id is not None:
            reply = connection.scalar(select(messages.c.content).where(
                messages.c.id == reply_id,
                messages.c.conversation_id == run["conversation_id"],
                messages.c.role == "user",
            ))
            memory = run["memory_json"]
            if (not reply or not isinstance(memory, dict)
                    or not isinstance(memory.get("last_answer"), str)
                    or not memory.get("pending_clarification")):
                raise ConversationContextError("memory_scope")
            if "recent_turns" not in memory:
                history.append((HumanMessage(content=original),
                                AIMessage(content=memory["last_answer"])))
            return ConversationContext(reply, memory, history[-4:])
        if not original:
            raise ConversationContextError("memory_scope")
        return ConversationContext(original, memory, history[-4:])
