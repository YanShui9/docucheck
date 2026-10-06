"""Server-authorized memory envelope; no tool argument can change its identity or scope."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine

from ..api.auth import Principal
from ..evidence.repository import digest
from .conversation_context import load_conversation_context
from .memory_repository import MemoryError, MemoryRepository
from .run_repository import RunRepository


@dataclass
class MemoryContext:
    layers: dict
    task_memory: dict | None
    manifest: dict
    validate: Callable[[], None]
    history_reader: Callable


def load_memory_context(engine: Engine, repo, run, request, context) -> MemoryContext:
    service, runs = MemoryRepository(engine), RunRepository(engine)
    actor = request["requester_key"] or Principal("local", "local").key

    def access():
        if not runs.has_source_access(run.run_id, run.active_attempt_id, run.fence):
            raise MemoryError("memory_context_changed")

    access()
    profile = service.profile(actor)
    conversation = service.conversation(
        repo.case_id,
        request["conversation_id"],
        repo.snapshot_id,
        exclude_run_id=run.run_id,
        before=context.cutoff,
        own_run_id=run.run_id,
        before_sequence=context.cutoff_sequence,
    )
    task, summary = conversation["task"], conversation["summary"]
    fingerprint = digest([profile, conversation])

    def recent_fingerprint(loaded):
        return digest(
            [
                loaded.question,
                loaded.current_message_id,
                loaded.message_ids,
                [[q.content, a.content] for q, a in loaded.history],
                loaded.memory,
            ]
        )

    recent_digest = recent_fingerprint(context)

    def validate():
        access()
        try:
            current = service.conversation(
                repo.case_id,
                request["conversation_id"],
                repo.snapshot_id,
                exclude_run_id=run.run_id,
                before=context.cutoff,
                own_run_id=run.run_id,
                before_sequence=context.cutoff_sequence,
            )
            if digest([service.profile(actor), current]) != fingerprint:
                raise MemoryError("memory_context_changed")
            loaded = load_conversation_context(
                engine, run.run_id, run.active_attempt_id
            )
            if recent_fingerprint(loaded) != recent_digest:
                raise MemoryError("memory_context_changed")
        except ValueError:
            raise ValueError("memory_context_changed") from None
        access()

    def read_history(ids, **kwargs):
        validate()
        data = service.read_history(
            repo.case_id,
            request["conversation_id"],
            repo.snapshot_id,
            ids,
            before=context.cutoff,
            own_run_id=run.run_id,
            before_sequence=context.cutoff_sequence,
            **kwargs,
        )
        validate()
        manifest.setdefault("history_reads", []).extend(
            {
                "message_id": item["message_id"],
                "run_id": item["run_id"],
                "part": item["part"],
                "start_char": kwargs.get("start_char", 0),
                "next_char": item["next_char"],
                "text_digest": digest(item["text"]),
                "truncated": item["truncated"],
            }
            for item in data
        )
        return data

    manifest = {
        "version": 1,
        "case_id": repo.case_id,
        "conversation_id": request["conversation_id"],
        "snapshot_id": repo.snapshot_id,
        "revision": repo.revision,
        "current_message_id": context.current_message_id,
        "history_cutoff": context.cutoff.isoformat() if context.cutoff else None,
        "history_cutoff_sequence": context.cutoff_sequence,
        "resumed_memory_digest": digest(context.memory) if context.resumed else None,
        "restored_checkpoint_id": context.own_checkpoint_id
        or (task["id"] if task else None),
        "restored_memory_digest": digest(
            context.memory
            if context.resumed or context.own_checkpoint_id
            else task["memory"]
            if task
            else context.memory
        ),
        "history_source_message_ids": context.message_ids,
        "task_memory_id": task["id"] if task else None,
        "task_memory_version": task["version"] if task else None,
        "summary_id": summary["id"] if summary else None,
        "summary_version": summary["version"] if summary else None,
        "preference_revision": profile["revision"],
        "preference_fingerprint": digest(profile),
    }
    return MemoryContext(
        {
            "preferences": {r["key"]: r["value"] for r in profile["records"]},
            "summary": summary,
        },
        task["memory"]
        if task and not context.resumed and not context.own_checkpoint_id
        else None,
        manifest,
        validate,
        read_history,
    )
