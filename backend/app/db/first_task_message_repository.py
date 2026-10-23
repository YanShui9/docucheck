"""Frozen first-user-message lookup within the fenced Assistant's own Case.

This is dialogue recall, not evidence, a current rule, or execution permission.
"""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, insert, or_, select

from ..evidence.repository import digest
from .memory_repository import MemoryError, _clip
from .models import (
    agent_runs,
    conversation_summaries,
    conversations,
    input_snapshots,
    messages,
    run_events,
)
from .run_repository import _now
from .task_handoff_repository import TaskHandoffRepository

KIND = "first_task_message_v1"


def _fail():
    raise MemoryError("memory_context_changed")


class FirstTaskMessageRepository(TaskHandoffRepository):
    @staticmethod
    def _queued_time():
        return (
            select(run_events.c.summary_json["task_handoff_cutoff_at"].as_string())
            .where(run_events.c.run_id == agent_runs.c.id, run_events.c.kind == "queued")
            .order_by(run_events.c.seq)
            .limit(1)
            .correlate(agent_runs)
            .scalar_subquery()
        )

    @staticmethod
    def _query(run):
        return (
            select(messages, conversations.c.created_at.label("conversation_created_at"),
                   agent_runs.c.id.label("source_run_id"), agent_runs.c.snapshot_id,
                   input_snapshots.c.revision)
            .join(conversations, conversations.c.id == messages.c.conversation_id)
            .join(agent_runs, and_(agent_runs.c.message_id == messages.c.id,
                                  agent_runs.c.conversation_id == conversations.c.id))
            .join(input_snapshots, and_(input_snapshots.c.id == agent_runs.c.snapshot_id,
                                       input_snapshots.c.case_id == run["case_id"]))
            .where(conversations.c.case_id == run["case_id"],
                   agent_runs.c.case_id == run["case_id"], messages.c.role == "user",
                   messages.c.id != run["message_id"], agent_runs.c.id != run["id"])
        )

    def _source(self, connection, run, cutoff, message_id):
        row = connection.execute(self._query(run).where(messages.c.id == message_id)).mappings().one_or_none()
        if row is None or not isinstance(row["content"], str):
            _fail()
        owner = connection.execute(select(agent_runs).where(agent_runs.c.id == row["source_run_id"])).mappings().one()
        stamp = connection.scalar(select(self._queued_time()).select_from(agent_runs).where(agent_runs.c.id == owner["id"]))
        submitted = self._cutoff(connection, owner)
        if stamp is not None:
            # Reuse B2's strict canonical timestamp validation. Never adopt a
            # later retry's queued event or silently accept a malformed field.
            if submitted >= cutoff:
                _fail()
        elif row["created_at"] + timedelta(seconds=1) > cutoff:
            # Legacy DATETIME(0) can round either direction; only a full-second
            # upper bound proves this old message predates the current Run.
            _fail()
        source = {key: row[key] for key in ("conversation_id", "sequence", "snapshot_id", "revision", "source_run_id")}
        source.update(message_id=row["id"], created_at=row["created_at"].isoformat(),
                      conversation_created_at=row["conversation_created_at"].isoformat(),
                      submitted_at=stamp, content_digest=digest(row["content"]))
        return source, row["content"]

    def _record(self, connection, run):
        return connection.execute(select(conversation_summaries).where(
            conversation_summaries.c.case_id == run["case_id"],
            conversation_summaries.c.conversation_id == run["conversation_id"],
            conversation_summaries.c.summary_json["kind"].as_string() == KIND,
            conversation_summaries.c.summary_json["source_run_id"].as_string() == run["id"],
        ).with_for_update()).mappings().one_or_none()

    def _checked(self, connection, run, scope, cutoff, record):
        try:
            body = record["summary_json"]
            fields = {key: body[key] for key in ("kind", "source_run_id", "scope", "cutoff_at", "source", "conversation_number")}
            if (body["kind"] != KIND or body["source_run_id"] != run["id"]
                    or body["scope"] != scope or record["snapshot_id"] != run["snapshot_id"]
                    or body["cutoff_at"] != cutoff.isoformat(timespec="microseconds")
                    or digest(fields) != body["input_digest"]
                    or digest(body["source"]) != record["source_digest"]):
                _fail()
            content = None
            if body["source"] is not None:
                source, content = self._source(connection, run, cutoff, body["source"]["message_id"])
                if source != body["source"] or type(body["conversation_number"]) is not int or body["conversation_number"] < 1:
                    _fail()
            elif body["conversation_number"] is not None:
                _fail()
            view = {"version": 1, "record_id": record["id"], "record_version": record["version"],
                    "input_digest": body["input_digest"], "cutoff_at": body["cutoff_at"],
                    "source": body["source"], "conversation_number": body["conversation_number"]}
            return view, content
        except (KeyError, TypeError, AttributeError):
            _fail()

    def prepare(self, claim):
        with self._transaction() as connection:
            run, _, scope = self._guard(connection, claim)
            cutoff = self._cutoff(connection, run)
            record = self._record(connection, run)
            if record is not None:
                return self._checked(connection, run, scope, cutoff, record)[0]
            queued = self._queued_time()
            first = connection.execute(self._query(run).where(or_(
                queued < cutoff.isoformat(timespec="microseconds"),
                and_(queued.is_(None), messages.c.created_at <= cutoff - timedelta(seconds=1)),
            )).order_by(messages.c.created_at, queued, conversations.c.created_at, conversations.c.id,
                        messages.c.sequence, messages.c.id, agent_runs.c.id).limit(1)).mappings().first()
            source, number = None, None
            if first is not None:
                source, _ = self._source(connection, run, cutoff, first["id"])
                number = connection.scalar(select(func.count()).select_from(conversations).where(
                    conversations.c.case_id == run["case_id"],
                    or_(conversations.c.created_at < first["conversation_created_at"],
                        and_(conversations.c.created_at == first["conversation_created_at"],
                             conversations.c.id <= first["conversation_id"]))))
            body = {"kind": KIND, "source_run_id": run["id"], "scope": scope,
                    "cutoff_at": cutoff.isoformat(timespec="microseconds"),
                    "source": source, "conversation_number": number}
            body["input_digest"] = digest(body)
            version = connection.scalar(select(func.coalesce(func.max(conversation_summaries.c.version), 0)).where(
                conversation_summaries.c.conversation_id == run["conversation_id"])) + 1
            record = {"id": uuid4().hex, "case_id": run["case_id"], "conversation_id": run["conversation_id"],
                      "snapshot_id": run["snapshot_id"], "version": version, "source_digest": digest(source),
                      "summary_json": body, "created_at": _now()}
            connection.execute(insert(conversation_summaries).values(**record))
            return self._checked(connection, run, scope, cutoff, record)[0]

    def _validated(self, connection, claim, view):
        run, _, scope = self._guard(connection, claim)
        record = self._record(connection, run)
        if record is None:
            _fail()
        current, content = self._checked(connection, run, scope, self._cutoff(connection, run), record)
        if current != view:
            _fail()
        return content

    def validate(self, claim, view):
        with self._transaction() as connection:
            self._validated(connection, claim, view)

    def read(self, claim, view, *, start_char=0):
        if type(start_char) is not int or start_char < 0:
            raise MemoryError("history_scope")
        with self._transaction() as connection:
            content = self._validated(connection, claim, view)
            if content is None:
                if start_char:
                    raise MemoryError("history_scope")
                return None
            if start_char >= len(content):
                raise MemoryError("history_scope")
            text = _clip(content[start_char:], 1600)
            end = start_char + len(text)
            return {"text": text, "start_char": start_char, "total_chars": len(content),
                    "next_char": end if end < len(content) else None,
                    "conversation_number": view["conversation_number"],
                    "created_at": view["source"]["created_at"], "revision": view["source"]["revision"]}
