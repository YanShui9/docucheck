"""Immutable semantic conversation notes, guarded against their original SQL phases.

Notes are auxiliary dialogue context. They never authorize evidence or rules.
"""

import json
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import func, insert, select

from ..evidence.repository import digest
from ..runtime.lifecycle import RunStatus
from .assistant_repository import AssistantRepository
from .conversation_turns import load_turns
from .memory_repository import RECENT_TURNS, MemoryError
from .models import (
    agent_runs,
    cases,
    conversation_summaries,
    messages,
    project_members,
    run_attempts,
    run_events,
    task_memory,
)
from .run_repository import RunRepository, _now

KIND = "semantic_memory_v1"
NOTE_KINDS = {"goal", "decision", "open_question", "next_step"}
_CURRENT_HEAD = object()


def _fail(code="semantic_memory_changed"):
    raise MemoryError(code)


def _encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _manifest(source):
    return {
        key: value for key, value in source.items() if key not in {"question", "answer"}
    }


def _notes(notes, sources):
    ids = {source["message_id"] for source in sources}
    if not isinstance(notes, list) or not 1 <= len(notes) <= 8:
        _fail("invalid_semantic_memory")
    for note in notes:
        if (
            not isinstance(note, dict)
            or set(note) != {"kind", "text", "source_message_ids"}
            or not isinstance(note["kind"], str)
            or note["kind"] not in NOTE_KINDS
            or not isinstance(note["text"], str)
            or not note["text"].strip()
            or not 1 <= len(note["text"]) <= 300
        ):
            _fail("invalid_semantic_memory")
        refs = note["source_message_ids"]
        if (
            not isinstance(refs, list)
            or not 1 <= len(refs) <= 2
            or any(not isinstance(ref, str) or ref not in ids for ref in refs)
            or len(set(refs)) != len(refs)
        ):
            _fail("invalid_semantic_memory")
    if len(_encoded({"notes": notes})) > 3500:
        _fail("invalid_semantic_memory")


class SemanticMemoryRepository:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def _transaction(self):
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "mysql":
                # Assistant._guard reads the Run before locking its Case. Under
                # RR that first read could freeze a snapshot predating a writer
                # whose Case lock we then wait for. Fresh statement snapshots
                # plus the existing Case-first locks keep source/CAS rechecks
                # current. SQLAlchemy restores pooled isolation on close.
                connection = connection.execution_options(
                    isolation_level="READ COMMITTED"
                )
            with connection.begin():
                yield connection

    def _guard(self, connection, claim):
        binding = AssistantRepository(self.engine)._guard(connection, claim)
        run = (
            connection.execute(
                select(agent_runs).where(agent_runs.c.id == claim.run_id)
            )
            .mappings()
            .one()
        )
        attempt = (
            connection.execute(
                select(run_attempts)
                .where(
                    run_attempts.c.id == claim.active_attempt_id,
                    run_attempts.c.run_id == claim.run_id,
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if attempt is None or attempt["status"] != "running":
            _fail()
        project_id = connection.scalar(
            select(cases.c.project_id).where(cases.c.id == run["case_id"])
        )
        if (
            project_id is not None
            and connection.scalar(
                select(project_members.c.role)
                .where(
                    project_members.c.project_id == project_id,
                    project_members.c.principal_key == run["requester_key"],
                )
                .with_for_update()
            )
            is None
        ):
            _fail("source_access_changed")
        sequence = connection.scalar(
            select(messages.c.sequence).where(
                messages.c.id == run["message_id"],
                messages.c.conversation_id == run["conversation_id"],
                messages.c.role == "user",
            )
        )
        if type(sequence) is not int:
            _fail()
        scope = {
            key: binding[key]
            for key in (
                "case_id",
                "snapshot_id",
                "checklist_revision",
                "checklist_version_id",
                "base_digest",
                "item_binding_version_id",
                "item_binding_digest",
            )
        }
        scope["conversation_id"] = run["conversation_id"]
        return dict(run), sequence, scope

    def _sources(self, connection, run, sequence, ids):
        if not ids:
            return []
        if len(set(ids)) != len(ids):
            _fail()
        try:
            rows = load_turns(
                connection,
                run["case_id"],
                run["conversation_id"],
                run["snapshot_id"],
                task_ids=ids,
                limit=len(ids),
                before_sequence=sequence,
                exclude_run_id=run["id"],
            )
        except (ValueError, KeyError, TypeError):
            _fail()
        if len(rows) != len(ids):
            _fail()
        sources = []
        for row in rows:
            owner = (
                connection.execute(
                    select(
                        agent_runs.c.case_id,
                        agent_runs.c.conversation_id,
                        agent_runs.c.snapshot_id,
                    ).where(agent_runs.c.id == row["id"])
                )
                .mappings()
                .one()
            )
            if (
                any(
                    owner[key] != run[key]
                    for key in ("case_id", "conversation_id", "snapshot_id")
                )
                or row["sequence"] >= sequence
            ):
                _fail()
            sources.append(
                {
                    "task_memory_id": row["task_memory_id"],
                    "message_id": row["message_id"],
                    "run_id": row["id"],
                    "version": row["version"],
                    "question": row["content"],
                    "answer": row["answer_text"],
                    "phase_digest": digest(row["state"]),
                    "question_digest": digest(row["content"]),
                    "answer_digest": digest(row["answer_text"]),
                }
            )
        return sorted(sources, key=lambda source: source["version"])

    def _head(self, connection, run, sequence, scope_digest):
        return (
            connection.execute(
                select(conversation_summaries)
                .where(
                    conversation_summaries.c.case_id == run["case_id"],
                    conversation_summaries.c.conversation_id == run["conversation_id"],
                    conversation_summaries.c.snapshot_id == run["snapshot_id"],
                    conversation_summaries.c.summary_json["kind"].as_string() == KIND,
                    conversation_summaries.c.summary_json["scope_digest"].as_string()
                    == scope_digest,
                    (
                        conversation_summaries.c.summary_json[
                            "source_sequence"
                        ].as_integer()
                        < sequence
                    )
                    | (
                        conversation_summaries.c.summary_json[
                            "source_run_id"
                        ].as_string()
                        == run["id"]
                    ),
                )
                .order_by(conversation_summaries.c.version.desc())
                .limit(1)
                .with_for_update()
            )
            .mappings()
            .first()
        )

    def _read_head(self, connection, head, run, sequence, scope):
        if head is None:
            return None, []
        payload = head["summary_json"]
        try:
            unsigned = {
                key: value for key, value in payload.items() if key != "payload_digest"
            }
            if (
                payload["scope"] != scope
                or payload["scope_digest"] != digest(scope)
                or digest(unsigned) != payload["payload_digest"]
                or type(payload["through_version"]) is not int
                or payload["through_version"] < 1
            ):
                _fail()
            manifest = payload["sources"]
            sources = self._sources(
                connection,
                run,
                sequence,
                [source["task_memory_id"] for source in manifest],
            )
            if (
                [_manifest(source) for source in sources] != manifest
                or digest(manifest) != head["source_digest"]
                or any(
                    source["version"] > payload["through_version"] for source in sources
                )
            ):
                _fail()
            _notes(payload["notes"], sources)
            if {
                ref for note in payload["notes"] for ref in note["source_message_ids"]
            } != {source["message_id"] for source in sources}:
                _fail()
        except (KeyError, TypeError, AttributeError):
            _fail()
        return {
            "id": head["id"],
            "version": head["version"],
            "notes": payload["notes"],
            "through_version": payload["through_version"],
        }, sources

    def _prepare(self, connection, claim, *, head=_CURRENT_HEAD):
        run, sequence, scope = self._guard(connection, claim)
        scope_digest = digest(scope)
        if head is _CURRENT_HEAD:
            head = self._head(connection, run, sequence, scope_digest)
        summary, previous_sources = self._read_head(
            connection, head, run, sequence, scope
        )
        watermark = summary["through_version"] if summary else 0
        # SQL chooses the oldest pending phases, without loading an unbounded
        # conversation or skipping old phases once the recent window advances.
        eligible = (
            select(task_memory.c.id, task_memory.c.version)
            .join(agent_runs, agent_runs.c.id == task_memory.c.source_run_id)
            .join(
                messages,
                messages.c.id
                == task_memory.c.state_json["source_message_id"].as_string(),
            )
            .where(
                task_memory.c.case_id == run["case_id"],
                task_memory.c.conversation_id == run["conversation_id"],
                task_memory.c.snapshot_id == run["snapshot_id"],
                task_memory.c.source_run_id != run["id"],
                agent_runs.c.case_id == run["case_id"],
                agent_runs.c.conversation_id == run["conversation_id"],
                agent_runs.c.snapshot_id == run["snapshot_id"],
                messages.c.sequence < sequence,
                messages.c.conversation_id == run["conversation_id"],
            )
        )
        recent = (
            eligible.order_by(task_memory.c.version.desc())
            .limit(RECENT_TURNS)
            .subquery()
        )
        pending = eligible.where(
            task_memory.c.id.not_in(select(recent.c.id)),
            task_memory.c.version > watermark,
        ).subquery()
        pending_count = connection.scalar(select(func.count()).select_from(pending))
        new_ids = list(
            connection.scalars(
                select(pending.c.id).order_by(pending.c.version).limit(4)
            )
        )
        new_sources = self._sources(connection, run, sequence, new_ids)
        sources = sorted(
            previous_sources + new_sources, key=lambda source: source["version"]
        )
        through = max([watermark] + [source["version"] for source in new_sources])
        stable = {
            "scope_digest": scope_digest,
            "previous_id": summary["id"] if summary else None,
            "summary": summary,
            "sources": sources,
            "pending_count": pending_count,
            "through_version": through,
        }
        # Attempt registration changes no authorized input identity.
        attempted = (
            connection.scalar(
                select(run_events.c.id)
                .where(
                    run_events.c.run_id == claim.run_id,
                    run_events.c.kind == "semantic_summary_started",
                )
                .limit(1)
            )
            is not None
        )
        return stable | {"input_digest": digest(stable), "attempted": attempted}, (
            run,
            sequence,
            scope,
        )

    def prepare(self, claim):
        with self._transaction() as connection:
            return self._prepare(connection, claim)[0]

    @staticmethod
    def _cas_head(connection, run, scope_digest, expected):
        latest = connection.scalar(
            select(conversation_summaries.c.id)
            .where(
                conversation_summaries.c.case_id == run["case_id"],
                conversation_summaries.c.conversation_id == run["conversation_id"],
                conversation_summaries.c.snapshot_id == run["snapshot_id"],
                conversation_summaries.c.summary_json["kind"].as_string() == KIND,
                conversation_summaries.c.summary_json["scope_digest"].as_string()
                == scope_digest,
            )
            .order_by(conversation_summaries.c.version.desc())
            .limit(1)
            .with_for_update()
        )
        # Future heads are not model input, but an old worker may not overwrite
        # them merely because its immutable temporal cutoff hides them.
        if latest != expected:
            _fail()

    @staticmethod
    def _same(view, current):
        if (
            not isinstance(view, dict)
            or view.get("input_digest") != current["input_digest"]
            or view.get("scope_digest") != current["scope_digest"]
            or view.get("previous_id") != current["previous_id"]
            or digest(
                {
                    k: v
                    for k, v in view.items()
                    if k not in {"input_digest", "attempted"}
                }
            )
            != view.get("input_digest")
        ):
            _fail()

    def begin_attempt(self, claim, view):
        with self._transaction() as connection:
            current, (run, _, _) = self._prepare(connection, claim)
            self._same(view, current)
            self._cas_head(
                connection, run, current["scope_digest"], current["previous_id"]
            )
            if current["attempted"]:
                return False
            RunRepository(self.engine)._append_event(
                connection,
                claim.run_id,
                claim.active_attempt_id,
                "semantic_summary_started",
                RunStatus.RUNNING,
                _now(),
                summary={"version": 1, "input_digest": current["input_digest"]},
            )
        return True

    def save(self, claim, view, notes):
        with self._transaction() as connection:
            current, (run, sequence, scope) = self._prepare(connection, claim)
            self._same(view, current)
            self._cas_head(
                connection, run, current["scope_digest"], current["previous_id"]
            )
            _notes(notes, current["sources"])
            content = {
                "prior_notes": current["summary"]["notes"]
                if current["summary"]
                else [],
                "sources": [
                    {"question": source["question"], "answer": source["answer"]}
                    for source in current["sources"]
                ],
            }
            if not current["sources"] or len(_encoded({"notes": notes})) >= len(
                _encoded(content)
            ):
                _fail("invalid_semantic_memory")
            refs = {ref for note in notes for ref in note["source_message_ids"]}
            manifest = [
                _manifest(source)
                for source in current["sources"]
                if source["message_id"] in refs
            ]
            payload = {
                "kind": KIND,
                "scope": scope,
                "scope_digest": current["scope_digest"],
                "previous_id": current["previous_id"],
                "input_digest": current["input_digest"],
                "through_version": current["through_version"],
                "notes": notes,
                "sources": manifest,
                "source_run_id": claim.run_id,
                "source_sequence": sequence,
            }
            payload["payload_digest"] = digest(payload)
            version = (
                connection.scalar(
                    select(
                        func.coalesce(func.max(conversation_summaries.c.version), 0)
                    ).where(
                        conversation_summaries.c.conversation_id
                        == run["conversation_id"]
                    )
                )
                + 1
            )
            identity = uuid4().hex
            connection.execute(
                insert(conversation_summaries).values(
                    id=identity,
                    case_id=run["case_id"],
                    conversation_id=run["conversation_id"],
                    snapshot_id=run["snapshot_id"],
                    version=version,
                    source_digest=digest(manifest),
                    summary_json=payload,
                    created_at=_now(),
                )
            )
            return {
                "id": identity,
                "version": version,
                "notes": notes,
                "through_version": current["through_version"],
            }

    def validate(self, claim, view, summary=None):
        """Validate an original view, optionally accepting the exact head it saved."""
        with self._transaction() as connection:
            current, (run, sequence, _scope) = self._prepare(connection, claim)
            self._cas_head(
                connection, run, current["scope_digest"], current["previous_id"]
            )
            if summary is None:
                self._same(view, current)
                return
            if current["summary"] != summary:
                _fail()
            head = self._head(connection, run, sequence, current["scope_digest"])
            payload = head["summary_json"]
            if (
                payload["input_digest"] != view.get("input_digest")
                or payload["previous_id"] != view.get("previous_id")
                or current["scope_digest"] != view.get("scope_digest")
                or payload["source_run_id"] != claim.run_id
            ):
                _fail()
            previous = None
            if view["previous_id"] is not None:
                previous = (
                    connection.execute(
                        select(conversation_summaries)
                        .where(
                            conversation_summaries.c.id == view["previous_id"],
                            conversation_summaries.c.case_id == run["case_id"],
                            conversation_summaries.c.conversation_id
                            == run["conversation_id"],
                            conversation_summaries.c.snapshot_id == run["snapshot_id"],
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
                if previous is None:
                    _fail()
            # Reconstruct the original view against its immutable previous head.
            # This validates both dropped sources and changed pending phases.
            original, _ = self._prepare(connection, claim, head=previous)
            self._same(view, original)
