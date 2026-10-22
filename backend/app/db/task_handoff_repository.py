"""Immutable, deterministic historical task handoffs; never procurement authority."""

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, insert, or_, select

from ..evidence.repository import digest
from .assistant_repository import _confirmed_definition
from .item_binding_repository import load_run_bindings
from .memory_repository import MemoryError, _clip
from .models import (
    agent_runs,
    assistant_runs,
    conversation_summaries,
    messages,
    run_events,
    task_memory,
)
from .run_repository import _now
from .semantic_memory_repository import (
    KIND as SEMANTIC_KIND,
)
from .semantic_memory_repository import (
    SemanticMemoryRepository,
    _encoded,
    _manifest,
)

KIND = "task_handoff_v1"
MAX_BYTES = 3500
SCOPE_FIELDS = (
    "case_id",
    "snapshot_id",
    "checklist_revision",
    "checklist_version_id",
    "base_digest",
    "item_binding_version_id",
    "item_binding_digest",
)


def _fail(code="task_handoff_changed"):
    raise MemoryError(code)


class TaskHandoffRepository(SemanticMemoryRepository):
    """Reuse B1's Case-first transaction/attempt/access and original-phase guards."""

    @staticmethod
    def _scope(connection, run):
        binding = (
            connection.execute(
                select(assistant_runs).where(assistant_runs.c.run_id == run["id"])
            )
            .mappings()
            .first()
        )
        if (
            binding is None
            or any(binding[key] != run[key] for key in ("case_id", "snapshot_id"))
            or digest(binding["base_json"]) != binding["base_digest"]
        ):
            _fail()
        load_run_bindings(
            connection, binding, _confirmed_definition(connection, binding)
        )
        return {key: binding[key] for key in SCOPE_FIELDS} | {
            "conversation_id": run["conversation_id"]
        }

    @staticmethod
    def _cutoff(connection, run):
        root = connection.scalar(
            select(messages.c.created_at).where(
                messages.c.id == run["message_id"],
                messages.c.conversation_id == run["conversation_id"],
                messages.c.role == "user",
            )
        )
        summary = connection.scalar(
            select(run_events.c.summary_json)
            .where(run_events.c.run_id == run["id"], run_events.c.kind == "queued")
            .order_by(run_events.c.seq)
            .limit(1)
        )
        if root is None:
            _fail()
        # Legacy DATETIME(0) rounds up as well as down. Its stored second is
        # not an exact submission boundary; the preceding full second is a
        # conservative lower bound that cannot admit a later phase.
        legacy_cutoff = root.replace(microsecond=0) - timedelta(seconds=1)
        if summary is None:
            return legacy_cutoff
        if not isinstance(summary, dict):
            _fail()
        if "task_handoff_cutoff_at" not in summary:
            return legacy_cutoff
        value = summary["task_handoff_cutoff_at"]
        if not isinstance(value, str) or len(value) != 26:
            _fail()
        try:
            cutoff = datetime.fromisoformat(value)
        except ValueError:
            _fail()
        if (
            cutoff.tzinfo is not None
            or cutoff.isoformat(timespec="microseconds") != value
            or cutoff < legacy_cutoff
        ):
            _fail()
        return cutoff

    @staticmethod
    def _eligible(run, scope, cutoff, *, same_scope=False):
        query = (
            select(task_memory.c.id, task_memory.c.source_run_id)
            .join(agent_runs, agent_runs.c.id == task_memory.c.source_run_id)
            .join(assistant_runs, assistant_runs.c.run_id == agent_runs.c.id)
            .join(
                messages,
                messages.c.id
                == task_memory.c.state_json["source_message_id"].as_string(),
            )
            .where(
                task_memory.c.case_id == run["case_id"],
                agent_runs.c.case_id == run["case_id"],
                assistant_runs.c.case_id == run["case_id"],
                task_memory.c.conversation_id == agent_runs.c.conversation_id,
                task_memory.c.snapshot_id == agent_runs.c.snapshot_id,
                assistant_runs.c.snapshot_id == agent_runs.c.snapshot_id,
                messages.c.conversation_id == agent_runs.c.conversation_id,
                messages.c.role == "user",
                task_memory.c.source_run_id != run["id"],
                task_memory.c.created_at < cutoff,
                task_memory.c.state_json["status"]
                .as_string()
                .in_(
                    [
                        "completed",
                        "partial",
                        "waiting_user",
                    ]
                ),
            )
        )
        different = [
            agent_runs.c.conversation_id.is_distinct_from(scope["conversation_id"])
        ]
        different.extend(
            assistant_runs.c[key].is_distinct_from(scope[key]) for key in SCOPE_FIELDS
        )
        if same_scope:
            query = query.where(*[~condition for condition in different])
        else:
            query = query.where(or_(*different))
        return query

    def _phase(self, connection, current, cutoff, task_id):
        row = (
            connection.execute(select(task_memory).where(task_memory.c.id == task_id))
            .mappings()
            .first()
        )
        if (
            row is None
            or row["case_id"] != current["case_id"]
            or row["created_at"] >= cutoff
            or not isinstance(row["state_json"], dict)
        ):
            _fail()
        owner = (
            connection.execute(
                select(agent_runs).where(agent_runs.c.id == row["source_run_id"])
            )
            .mappings()
            .one()
        )
        original = (
            connection.execute(
                select(messages).where(
                    messages.c.id == row["state_json"].get("source_message_id"),
                    messages.c.conversation_id == owner["conversation_id"],
                )
            )
            .mappings()
            .first()
        )
        if original is None or owner["id"] == current["id"]:
            _fail()
        # B1 checks the full frozen phase, original question, response, owner,
        # and resume provenance. A synthetic excluded Run leaves the old owner
        # available while retaining B1's sequence-bound original-message check.
        source_run = dict(owner) | {"id": current["id"]}
        sources = super()._sources(
            connection, source_run, original["sequence"] + 1, [task_id]
        )
        source = sources[0]
        source.update(
            conversation_id=owner["conversation_id"],
            snapshot_id=owner["snapshot_id"],
            sequence=original["sequence"],
            scope=self._scope(connection, owner),
            status=row["state_json"]["status"],
            phase_created_at=row["created_at"].isoformat(),
            message_created_at=original["created_at"].isoformat(),
            run_created_at=owner["created_at"].isoformat(),
        )
        return source

    def _summary(self, connection, run, cutoff, source_scope):
        head = (
            connection.execute(
                select(conversation_summaries)
                .where(
                    conversation_summaries.c.case_id == run["case_id"],
                    conversation_summaries.c.conversation_id
                    == source_scope["conversation_id"],
                    conversation_summaries.c.snapshot_id == source_scope["snapshot_id"],
                    conversation_summaries.c.summary_json["kind"].as_string()
                    == SEMANTIC_KIND,
                    conversation_summaries.c.summary_json["scope_digest"].as_string()
                    == digest(source_scope),
                    conversation_summaries.c.created_at < cutoff,
                )
                .order_by(conversation_summaries.c.version.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if head is None:
            return None, [], []
        return self._summary_record(connection, run, cutoff, head, source_scope)

    def _summary_record(self, connection, run, cutoff, head, source_scope):
        payload = head["summary_json"]
        owner = (
            connection.execute(
                select(agent_runs).where(
                    agent_runs.c.id == payload.get("source_run_id"),
                    agent_runs.c.case_id == run["case_id"],
                )
            )
            .mappings()
            .first()
        )
        if (
            owner is None
            or self._scope(connection, owner) != source_scope
            or head["created_at"] >= cutoff
        ):
            _fail()
        summary, old_sources = super()._read_head(
            connection, head, dict(owner), payload["source_sequence"], source_scope
        )
        candidates = [
            note
            for note in summary["notes"]
            if note["kind"]
            in {
                "goal",
                "open_question",
                "next_step",
            }
        ]
        notes, refs = [], set()
        for note in candidates:
            selected = refs | set(note["source_message_ids"])
            # Preserve whole notes and every original phase supporting them.
            # B1 may contain more than eight original phases; the handoff's
            # explicit read whitelist remains bounded to eight plus two raw
            # goal/recent phases even for repeated source message identities.
            if sum(source["message_id"] in selected for source in old_sources) <= 8:
                notes.append(note)
                refs = selected
        if not notes:
            return None, [], []
        sources = [
            self._phase(connection, run, cutoff, source["task_memory_id"])
            for source in old_sources
            if source["message_id"] in refs
        ]
        return (
            {
                "id": head["id"],
                "version": head["version"],
                "payload_digest": payload["payload_digest"],
            },
            notes,
            sources,
        )

    @staticmethod
    def _context(scope, latest, goal, notes):
        def handle(source):
            return {
                "message_id": source["message_id"],
                "task_memory_id": source["task_memory_id"],
            }

        context = {
            "version": 1,
            "current_scope": scope,
            "source_scope": latest["scope"],
            "changed_fields": [
                key for key in scope if scope[key] != latest["scope"][key]
            ],
            "goal": handle(goal),
            "recent_request": handle(latest),
            "notes": [],
            "last_processing_status": latest["status"],
        }
        if len(_encoded(context)) > MAX_BYTES:
            return None
        # Each optional value is kept whole. A long original is represented
        # solely by its handle, never by a misleading truncated goal.
        for key, source in [("recent_request", latest), ("goal", goal)]:
            context[key]["text"] = source["question"]
            if len(_encoded(context)) > MAX_BYTES:
                del context[key]["text"]
        for note in notes:
            context["notes"].append(note)
            if len(_encoded(context)) > MAX_BYTES:
                context["notes"].pop()
        return context

    @staticmethod
    def _view(record):
        payload = record["summary_json"]
        return {
            "context": payload["context"],
            "input_digest": payload["input_digest"],
            "manifest": {
                "version": 1,
                "record_id": record["id"],
                "record_version": record["version"],
                "input_digest": payload["input_digest"],
                "cutoff_at": payload["cutoff_at"],
                "sources": payload["sources"],
                "summary": payload["summary"],
            },
        }

    def _record(self, connection, run):
        return (
            connection.execute(
                select(conversation_summaries)
                .where(
                    conversation_summaries.c.case_id == run["case_id"],
                    conversation_summaries.c.conversation_id == run["conversation_id"],
                    conversation_summaries.c.summary_json["kind"].as_string() == KIND,
                    conversation_summaries.c.summary_json["source_run_id"].as_string()
                    == run["id"],
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )

    def _checked(self, connection, run, scope, cutoff, record):
        try:
            payload = record["summary_json"]
            if (
                payload["kind"] != KIND
                or payload["scope"] != scope
                or payload["source_run_id"] != run["id"]
                or payload["cutoff_at"] != cutoff.isoformat(timespec="microseconds")
                or record["snapshot_id"] != run["snapshot_id"]
                or digest({k: v for k, v in payload.items() if k != "payload_digest"})
                != payload["payload_digest"]
                or digest(payload["sources"]) != record["source_digest"]
                or digest(
                    {
                        k: payload[k]
                        for k in ("scope", "cutoff_at", "context", "sources", "summary")
                    }
                )
                != payload["input_digest"]
                or len(_encoded(payload["context"])) > MAX_BYTES
                or len(payload["sources"]) > 10
            ):
                _fail()
            sources = [
                self._phase(connection, run, cutoff, source["task_memory_id"])
                for source in payload["sources"]
            ]
            if [_manifest(source) for source in sources] != payload["sources"]:
                _fail()
            summary = payload["summary"]
            if summary is not None:
                head = (
                    connection.execute(
                        select(conversation_summaries).where(
                            conversation_summaries.c.id == summary["id"],
                            conversation_summaries.c.case_id == run["case_id"],
                        )
                    )
                    .mappings()
                    .first()
                )
                if head is None:
                    _fail()
                checked, _, _ = self._summary_record(
                    connection, run, cutoff, head, payload["context"]["source_scope"]
                )
                if checked != summary:
                    _fail()
        except (KeyError, TypeError, AttributeError, IndexError):
            _fail()
        return self._view(record), sources

    def prepare(self, claim):
        with self._transaction() as connection:
            run, _, scope = self._guard(connection, claim)
            cutoff = self._cutoff(connection, run)
            record = self._record(connection, run)
            if record is not None:
                return self._checked(connection, run, scope, cutoff, record)[0]
            candidate = (
                connection.execute(
                    self._eligible(run, scope, cutoff)
                    .order_by(
                        task_memory.c.created_at.desc(),
                        task_memory.c.version.desc(),
                        task_memory.c.id.desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            sources, summary, context = [], None, None
            if candidate is not None:
                latest = self._phase(connection, run, cutoff, candidate["id"])
                goal_row = (
                    connection.execute(
                        self._eligible(run, latest["scope"], cutoff, same_scope=True)
                        .order_by(
                            task_memory.c.created_at,
                            task_memory.c.version,
                            task_memory.c.id,
                        )
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                goal = self._phase(connection, run, cutoff, goal_row["id"])
                summary, notes, old_sources = self._summary(
                    connection, run, cutoff, latest["scope"]
                )
                context = self._context(scope, latest, goal, notes)
                if context is not None:
                    retained_refs = {
                        ref
                        for note in context["notes"]
                        for ref in note["source_message_ids"]
                    }
                    old_sources = [
                        source
                        for source in old_sources
                        if source["message_id"] in retained_refs
                    ]
                    by_id = {
                        s["task_memory_id"]: s for s in [goal, latest] + old_sources
                    }
                    sources = [_manifest(source) for source in by_id.values()]
                else:
                    summary = None
            payload = {
                "kind": KIND,
                "source_run_id": run["id"],
                "scope": scope,
                "cutoff_at": cutoff.isoformat(timespec="microseconds"),
                "context": context,
                "sources": sources,
                "summary": summary,
            }
            payload["input_digest"] = digest(
                {
                    k: payload[k]
                    for k in ("scope", "cutoff_at", "context", "sources", "summary")
                }
            )
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
            record = {
                "id": uuid4().hex,
                "case_id": run["case_id"],
                "conversation_id": run["conversation_id"],
                "snapshot_id": run["snapshot_id"],
                "version": version,
                "source_digest": digest(sources),
                "summary_json": payload,
                "created_at": _now(),
            }
            connection.execute(insert(conversation_summaries).values(**record))
            return self._checked(connection, run, scope, cutoff, record)[0]

    def _validated(self, connection, claim, view):
        run, _, scope = self._guard(connection, claim)
        record = self._record(connection, run)
        if record is None:
            _fail()
        current, sources = self._checked(
            connection, run, scope, self._cutoff(connection, run), record
        )
        if view != current:
            _fail()
        return sources

    def validate(self, claim, view):
        with self._transaction() as connection:
            self._validated(connection, claim, view)

    def read_history(self, claim, view, message_ids, *, part="both", start_char=0):
        if (
            not isinstance(message_ids, list)
            or not 1 <= len(message_ids) <= 4
            or any(not isinstance(item, str) for item in message_ids)
            or len(set(message_ids)) != len(message_ids)
            or part not in {"question", "answer", "both"}
            or type(start_char) is not int
            or start_char < 0
            or (start_char and len(message_ids) != 1)
        ):
            _fail("history_scope")
        with self._transaction() as connection:
            sources = self._validated(connection, claim, view)
            if not set(message_ids) <= {source["message_id"] for source in sources}:
                _fail("history_scope")
            result = []
            for message_id in message_ids:
                phases = [
                    source for source in sources if source["message_id"] == message_id
                ]
                for source in phases:
                    for selected in (
                        ["question", "answer"] if part == "both" else [part]
                    ):
                        content = source[selected]
                        if start_char and start_char >= len(content):
                            continue
                        text = _clip(content[start_char:], 800)
                        next_char = start_char + len(text)
                        result.append(
                            {
                                "message_id": message_id,
                                "run_id": source["run_id"],
                                "task_memory_id": source["task_memory_id"],
                                "sequence": source["sequence"],
                                "conversation_id": source["conversation_id"],
                                "snapshot_id": source["snapshot_id"],
                                "source_scope": source["scope"],
                                "part": selected,
                                "text": text,
                                "is_historical": True,
                                "truncated": bool(start_char)
                                or next_char < len(content),
                                "next_char": next_char
                                if next_char < len(content)
                                else None,
                            }
                        )
            if not result:
                _fail("history_scope")
            return result
