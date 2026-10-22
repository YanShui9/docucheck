"""Bounded, source-linked checkpoints and explicitly confirmed personal preferences."""

import re
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Connection, Engine, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from ..evidence.repository import digest
from .conversation_turns import checkpoint_state, load_turns
from .models import (
    agent_runs,
    cases,
    conversation_summaries,
    conversations,
    input_snapshots,
    memory_profiles,
    memory_records,
    messages,
    task_memory,
)

PREFERENCES = {
    "answer_length": {"concise", "standard", "detailed"},
    "answer_format": {"paragraph", "bullets", "table"},
    "language": {"zh-CN", "en"},
}
RECENT_TURNS = 8
SUMMARY_ENTRIES = 20


class MemoryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _utc(value: datetime | None) -> str | None:
    return (
        value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z") if value else None
    )


def _clip(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _actor(actor: str) -> None:
    if not isinstance(actor, str) or not re.fullmatch(r"[a-f0-9]{64}", actor):
        raise MemoryError("invalid_memory_actor")


def next_sequence(connection: Connection, conversation_id: str) -> int:
    """Caller holds the parent Case lock, including resume and initial submissions."""
    return (
        connection.scalar(
            select(func.coalesce(func.max(messages.c.sequence), 0)).where(
                messages.c.conversation_id == conversation_id
            )
        )
        + 1
    )


def verify_profile(
    connection: Connection,
    actor: str,
    revision: int,
    fingerprint: str,
    *,
    now: datetime | None = None,
) -> None:
    """Terminal transaction locks the same profile row as PUT/DELETE."""
    actual_revision = (
        connection.scalar(
            select(memory_profiles.c.revision)
            .where(memory_profiles.c.principal_key == actor)
            .with_for_update()
        )
        or 0
    )
    rows = (
        connection.execute(
            select(memory_records)
            .where(
                memory_records.c.principal_key == actor,
                memory_records.c.expires_at.is_(None)
                | (memory_records.c.expires_at > _now(now)),
            )
            .order_by(memory_records.c.key)
        )
        .mappings()
        .all()
    )
    actual = {
        "revision": actual_revision,
        "records": [
            {"key": r["key"], "value": r["value"], "expires_at": _utc(r["expires_at"])}
            for r in rows
        ],
    }
    if actual_revision != revision or digest(actual) != fingerprint:
        raise MemoryError("memory_context_changed")


def _entry(row) -> dict:
    question, answer = row["content"], row["answer_text"] or ""
    return {
        "run_id": row["id"],
        "message_id": row["message_id"],
        "sequence": row["sequence"],
        "task_memory_id": row.get("task_memory_id"),
        "question_digest": digest(question),
        "answer_digest": digest(answer),
        "question_excerpt": _clip(question, 200),
        "answer_excerpt": _clip(answer, 240),
        "truncated": len(question.encode()) > 200 or len(answer.encode()) > 240,
    }


def _completed(case_id: str, conversation_id: str, snapshot_id: str):
    return (
        select(
            agent_runs.c.id,
            agent_runs.c.message_id,
            agent_runs.c.answer_text,
            messages.c.content,
            messages.c.sequence,
        )
        .join(
            messages,
            (messages.c.id == agent_runs.c.message_id)
            & (messages.c.conversation_id == agent_runs.c.conversation_id),
        )
        .where(
            agent_runs.c.case_id == case_id,
            agent_runs.c.conversation_id == conversation_id,
            agent_runs.c.snapshot_id == snapshot_id,
            agent_runs.c.status == "completed",
        )
    )


def _store_checkpoint(connection: Connection, run, current: datetime) -> None:
    memory = run["memory_json"]
    if not isinstance(memory, dict):
        return
    state = checkpoint_state(connection, run)
    fingerprint = digest(state)
    existing = connection.scalar(
        select(task_memory.c.id).where(
            task_memory.c.conversation_id == run["conversation_id"],
            task_memory.c.source_run_id == run["id"],
            task_memory.c.memory_digest == fingerprint,
        )
    )
    if existing is None:
        version = (
            connection.scalar(
                select(func.coalesce(func.max(task_memory.c.version), 0)).where(
                    task_memory.c.conversation_id == run["conversation_id"]
                )
            )
            + 1
        )
        connection.execute(
            insert(task_memory).values(
                id=uuid4().hex,
                case_id=run["case_id"],
                conversation_id=run["conversation_id"],
                snapshot_id=run["snapshot_id"],
                source_run_id=run["id"],
                version=version,
                memory_digest=fingerprint,
                state_json=state,
                created_at=current,
            )
        )


def save_checkpoint(
    connection: Connection, run_id: str, *, now: datetime | None = None
) -> None:
    """Fenced terminal transaction; bounded lazy backfill of pre-0013 memory."""
    run = (
        connection.execute(select(agent_runs).where(agent_runs.c.id == run_id))
        .mappings()
        .one()
    )
    if run["status"] not in {"completed", "partial", "waiting_user"} or not isinstance(
        run["memory_json"], dict
    ):
        return
    current = _now(now)
    has_checkpoint = connection.scalar(
        select(task_memory.c.id)
        .where(
            task_memory.c.case_id == run["case_id"],
            task_memory.c.conversation_id == run["conversation_id"],
            task_memory.c.snapshot_id == run["snapshot_id"],
        )
        .limit(1)
    )
    legacy = (
        []
        if has_checkpoint
        else connection.execute(
            select(agent_runs)
            .where(
                agent_runs.c.case_id == run["case_id"],
                agent_runs.c.conversation_id == run["conversation_id"],
                agent_runs.c.snapshot_id == run["snapshot_id"],
                agent_runs.c.status == "completed",
                agent_runs.c.id != run_id,
                ~select(task_memory.c.id)
                .where(
                    task_memory.c.conversation_id == agent_runs.c.conversation_id,
                    task_memory.c.source_run_id == agent_runs.c.id,
                )
                .exists(),
            )
            .order_by(agent_runs.c.created_at.desc(), agent_runs.c.id.desc())
            .limit(RECENT_TURNS + SUMMARY_ENTRIES)
        )
        .mappings()
        .all()
    )
    for old in reversed(legacy):
        _store_checkpoint(connection, old, old["updated_at"])
    _store_checkpoint(connection, run, current)
    rows = load_turns(
        connection, run["case_id"], run["conversation_id"], run["snapshot_id"]
    )[RECENT_TURNS:]
    if not rows:
        return
    entries = [_entry(row) for row in reversed(rows)]
    fingerprint = digest(entries)
    previous = (
        connection.execute(
            select(conversation_summaries)
            .where(
                conversation_summaries.c.conversation_id == run["conversation_id"],
                conversation_summaries.c.summary_json["kind"].as_string() == "source_excerpts_v1",
            )
            .order_by(conversation_summaries.c.version.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if (
        previous is not None
        and previous["snapshot_id"] == run["snapshot_id"]
        and previous["source_digest"] == fingerprint
    ):
        return
    count = connection.scalar(
        select(func.count())
        .select_from(task_memory)
        .where(
            task_memory.c.case_id == run["case_id"],
            task_memory.c.conversation_id == run["conversation_id"],
            task_memory.c.snapshot_id == run["snapshot_id"],
        )
    )
    count += connection.scalar(
        select(func.count())
        .select_from(agent_runs)
        .where(
            agent_runs.c.case_id == run["case_id"],
            agent_runs.c.conversation_id == run["conversation_id"],
            agent_runs.c.snapshot_id == run["snapshot_id"],
            agent_runs.c.status == "completed",
            ~select(task_memory.c.id)
            .where(
                task_memory.c.conversation_id == agent_runs.c.conversation_id,
                task_memory.c.source_run_id == agent_runs.c.id,
            )
            .exists(),
        )
    )
    connection.execute(
        insert(conversation_summaries).values(
            id=uuid4().hex,
            case_id=run["case_id"],
            conversation_id=run["conversation_id"],
            snapshot_id=run["snapshot_id"],
            version=connection.scalar(
                select(func.coalesce(func.max(conversation_summaries.c.version), 0)).where(
                    conversation_summaries.c.conversation_id == run["conversation_id"]
                )
            ) + 1,
            source_digest=fingerprint,
            summary_json={
                "kind": "source_excerpts_v1",
                "entries": entries,
                "source_run_id": run_id,
                "source_sequence": connection.scalar(
                    select(messages.c.sequence).where(
                        messages.c.id == run["message_id"]
                    )
                ),
                "older_turns_omitted": max(0, count - RECENT_TURNS - len(entries)),
            },
            created_at=current,
        )
    )


class MemoryRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def profile(self, actor: str, *, now: datetime | None = None) -> dict:
        _actor(actor)
        with self.engine.connect() as connection:
            revision = connection.scalar(
                select(memory_profiles.c.revision).where(
                    memory_profiles.c.principal_key == actor
                )
            )
            rows = (
                connection.execute(
                    select(memory_records)
                    .where(
                        memory_records.c.principal_key == actor,
                        (
                            memory_records.c.expires_at.is_(None)
                            | (memory_records.c.expires_at > _now(now))
                        ),
                    )
                    .order_by(memory_records.c.key)
                )
                .mappings()
                .all()
            )
        if any(
            row["key"] not in PREFERENCES or row["value"] not in PREFERENCES[row["key"]]
            for row in rows
        ):
            raise MemoryError("invalid_memory_preference")
        return {
            "revision": revision or 0,
            "records": [
                {
                    "key": row["key"],
                    "value": row["value"],
                    "expires_at": _utc(row["expires_at"]),
                }
                for row in rows
            ],
        }

    def _edit(
        self,
        actor: str,
        key: str,
        revision: int,
        value: str | None,
        expires_at: datetime | None,
        current: datetime,
    ) -> dict:
        _actor(actor)
        if type(revision) is not int or revision < 0 or key not in PREFERENCES:
            raise MemoryError("invalid_memory_preference")
        try:
            with self.engine.begin() as connection:
                profile = (
                    connection.execute(
                        select(memory_profiles)
                        .where(memory_profiles.c.principal_key == actor)
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
                if (profile["revision"] if profile else 0) != revision:
                    raise MemoryError("memory_revision_conflict")
                if profile is None:
                    connection.execute(
                        insert(memory_profiles).values(
                            principal_key=actor,
                            revision=0,
                            created_at=current,
                            updated_at=current,
                        )
                    )
                if value is None:
                    changed = connection.execute(
                        delete(memory_records).where(
                            memory_records.c.principal_key == actor,
                            memory_records.c.key == key,
                        )
                    ).rowcount
                else:
                    changed = 1
                    existing = connection.scalar(
                        select(memory_records.c.key).where(
                            memory_records.c.principal_key == actor,
                            memory_records.c.key == key,
                        )
                    )
                    values = {
                        "value": value,
                        "expires_at": expires_at,
                        "updated_at": current,
                    }
                    if existing:
                        connection.execute(
                            update(memory_records)
                            .where(
                                memory_records.c.principal_key == actor,
                                memory_records.c.key == key,
                            )
                            .values(**values)
                        )
                    else:
                        connection.execute(
                            insert(memory_records).values(
                                principal_key=actor, key=key, **values
                            )
                        )
                if changed:
                    connection.execute(
                        update(memory_profiles)
                        .where(
                            memory_profiles.c.principal_key == actor,
                            memory_profiles.c.revision == revision,
                        )
                        .values(revision=revision + 1, updated_at=current)
                    )
        except IntegrityError:
            raise MemoryError("memory_revision_conflict") from None
        except OperationalError as exc:
            # Concurrent first saves can take conflicting InnoDB gap locks.
            # This edit is rolled back; the caller refreshes its expected
            # revision rather than blindly repeating a confirmed write.
            if getattr(exc.orig, "args", (None,))[0] == 1213:
                raise MemoryError("memory_revision_conflict") from None
            raise
        return self.profile(actor, now=current)

    def put(
        self,
        actor: str,
        key: str,
        value: str,
        expected_revision: int,
        *,
        confirmed: bool,
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> dict:
        if confirmed is not True:
            raise MemoryError("memory_confirmation_required")
        if key not in PREFERENCES or value not in PREFERENCES[key]:
            raise MemoryError("invalid_memory_preference")
        current = _now(now)
        expiration = _now(expires_at) if expires_at else None
        if expiration is not None and expiration <= current:
            raise MemoryError("invalid_memory_expiration")
        return self._edit(actor, key, expected_revision, value, expiration, current)

    def delete(
        self,
        actor: str,
        key: str,
        expected_revision: int,
        *,
        now: datetime | None = None,
    ) -> dict:
        return self._edit(actor, key, expected_revision, None, None, _now(now))

    @staticmethod
    def _scope(
        connection: Connection, case_id: str, conversation_id: str, snapshot_id: str
    ) -> None:
        row = connection.execute(
            select(
                cases.c.revision,
                cases.c.deletion_requested_at,
                input_snapshots.c.revision.label("snapshot_revision"),
            )
            .join(conversations, conversations.c.case_id == cases.c.id)
            .join(input_snapshots, input_snapshots.c.case_id == cases.c.id)
            .where(
                cases.c.id == case_id,
                conversations.c.id == conversation_id,
                input_snapshots.c.id == snapshot_id,
            )
        ).first()
        if row is None:
            raise MemoryError("not_found")
        if row.deletion_requested_at is not None:
            raise MemoryError("case_deleting")
        if row.revision != row.snapshot_revision:
            raise MemoryError("memory_snapshot_changed")

    def conversation(
        self,
        case_id: str,
        conversation_id: str,
        snapshot_id: str,
        *,
        exclude_run_id: str | None = None,
        before=None,
        own_run_id=None,
        before_sequence=None,
    ) -> dict:
        with self.engine.connect() as connection:
            self._scope(connection, case_id, conversation_id, snapshot_id)
            try:
                tasks = load_turns(
                    connection,
                    case_id,
                    conversation_id,
                    snapshot_id,
                    limit=1,
                    exclude_run_id=exclude_run_id,
                    before=before,
                    before_sequence=before_sequence,
                )
            except ValueError:
                raise MemoryError("memory_source_changed") from None
            task = tasks[0] if tasks else None
            summary = (
                connection.execute(
                    select(conversation_summaries)
                    .where(
                        conversation_summaries.c.case_id == case_id,
                        conversation_summaries.c.conversation_id == conversation_id,
                        conversation_summaries.c.snapshot_id == snapshot_id,
                        conversation_summaries.c.summary_json["kind"].as_string() == "source_excerpts_v1",
                        (
                            (
                                conversation_summaries.c.summary_json[
                                    "source_sequence"
                                ].as_integer()
                                < before_sequence
                            )
                            | (
                                conversation_summaries.c.summary_json[
                                    "source_run_id"
                                ].as_string()
                                == own_run_id
                            )
                        )
                        if before_sequence is not None
                        else conversation_summaries.c.created_at <= before
                        if before
                        else True,
                    )
                    .order_by(conversation_summaries.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            public_summary = None
            if summary is not None:
                payload = summary["summary_json"]
                entries = payload.get("entries") if isinstance(payload, dict) else None
                if (
                    not isinstance(entries, list)
                    or not 1 <= len(entries) <= SUMMARY_ENTRIES
                    or payload.get("kind") != "source_excerpts_v1"
                    or type(payload.get("older_turns_omitted")) is not int
                    or not 0 <= payload["older_turns_omitted"] <= 2_147_483_647
                    or any(
                        not isinstance(e, dict)
                        or not isinstance(e.get("task_memory_id"), str)
                        for e in entries
                    )
                    or len({e["task_memory_id"] for e in entries}) != len(entries)
                ):
                    raise MemoryError("memory_source_changed")
                try:
                    rows = load_turns(
                        connection,
                        case_id,
                        conversation_id,
                        snapshot_id,
                        task_ids=[e["task_memory_id"] for e in entries],
                        limit=SUMMARY_ENTRIES,
                    )
                except ValueError:
                    raise MemoryError("memory_source_changed") from None
                by_id = {r["task_memory_id"]: _entry(r) for r in rows}
                if (
                    len(by_id) != len(entries)
                    or any(by_id.get(e["task_memory_id"]) != e for e in entries)
                    or digest(entries) != summary["source_digest"]
                ):
                    raise MemoryError("memory_source_changed")
                public_summary = {
                    "id": summary["id"],
                    "version": summary["version"],
                    **payload,
                }
            # The recent window can advance inside a resumed Run while its
            # temporal cutoff stays fixed. Derive the exact authorized view
            # from immutable phases, retaining the stored base's identity.
            try:
                eligible = load_turns(
                    connection,
                    case_id,
                    conversation_id,
                    snapshot_id,
                    before=before,
                    own_run_id=own_run_id,
                    before_sequence=before_sequence,
                )
            except ValueError:
                raise MemoryError("memory_source_changed") from None
            desired = [_entry(row) for row in reversed(eligible[RECENT_TURNS:])]
            if (
                (before is not None or before_sequence is not None)
                and desired
                and (public_summary is None or public_summary["entries"] != desired)
            ):
                view_digest = digest(desired)
                count = connection.scalar(
                    select(func.count())
                    .select_from(task_memory)
                    .join(agent_runs, agent_runs.c.id == task_memory.c.source_run_id)
                    .join(messages, messages.c.id == agent_runs.c.message_id)
                    .where(
                        task_memory.c.case_id == case_id,
                        task_memory.c.conversation_id == conversation_id,
                        task_memory.c.snapshot_id == snapshot_id,
                        (
                            (messages.c.sequence < before_sequence)
                            | (task_memory.c.source_run_id == own_run_id)
                        )
                        if before_sequence is not None
                        else (task_memory.c.created_at <= before)
                        | (task_memory.c.source_run_id == own_run_id),
                    )
                )
                count += connection.scalar(
                    select(func.count())
                    .select_from(agent_runs)
                    .join(messages, messages.c.id == agent_runs.c.message_id)
                    .where(
                        agent_runs.c.case_id == case_id,
                        agent_runs.c.conversation_id == conversation_id,
                        agent_runs.c.snapshot_id == snapshot_id,
                        agent_runs.c.status == "completed",
                        ~select(task_memory.c.id)
                        .where(
                            task_memory.c.conversation_id
                            == agent_runs.c.conversation_id,
                            task_memory.c.source_run_id == agent_runs.c.id,
                        )
                        .exists(),
                        messages.c.sequence < before_sequence
                        if before_sequence is not None
                        else agent_runs.c.updated_at <= before,
                    )
                )
                public_summary = {
                    "id": "view-" + view_digest,
                    "version": max(row["version"] for row in eligible),
                    "kind": "source_excerpts_v1",
                    "entries": desired,
                    "derived": True,
                    "base_summary_id": summary["id"] if summary else None,
                    "older_turns_omitted": max(0, count - RECENT_TURNS - len(desired)),
                }
            elif (before is not None or before_sequence is not None) and not desired:
                public_summary = None
        return {
            "task": (
                {
                    "id": task["task_memory_id"],
                    "version": task["version"],
                    "source_run_id": task["id"],
                    "state": {
                        k: task["state"][k] for k in ("focus", "pending_clarification")
                    },
                    "memory": task["state"]["checkpoint"],
                }
                if task
                else None
            ),
            "summary": public_summary,
        }

    def read_history(
        self,
        case_id: str,
        conversation_id: str,
        snapshot_id: str,
        message_ids: list[str],
        *,
        part: str,
        start_char: int = 0,
        before=None,
        own_run_id=None,
        before_sequence=None,
    ) -> list[dict]:
        if (
            not isinstance(message_ids, list)
            or not 1 <= len(message_ids) <= 4
            or any(not isinstance(item, str) for item in message_ids)
            or len(set(message_ids)) != len(message_ids)
            or part not in {"question", "answer"}
            or type(start_char) is not int
            or start_char < 0
            or (start_char and len(message_ids) != 1)
        ):
            raise MemoryError("history_scope")
        with self.engine.connect() as connection:
            try:
                self._scope(connection, case_id, conversation_id, snapshot_id)
            except MemoryError:
                raise MemoryError("history_scope") from None
            legacy = (
                connection.execute(
                    _completed(case_id, conversation_id, snapshot_id)
                    .where(
                        messages.c.sequence < before_sequence
                        if before_sequence is not None
                        else agent_runs.c.updated_at <= before
                        if before
                        else True
                    )
                    .where(messages.c.id.in_(message_ids))
                )
                .mappings()
                .all()
            )
            by_id = {r["message_id"]: r for r in legacy}
            for message_id in message_ids:
                try:
                    turns = load_turns(
                        connection,
                        case_id,
                        conversation_id,
                        snapshot_id,
                        limit=1,
                        message_ids=[message_id],
                        before=before,
                        own_run_id=own_run_id,
                        before_sequence=before_sequence,
                    )
                except ValueError:
                    raise MemoryError("history_scope") from None
                if turns:
                    by_id[message_id] = turns[0]
        if set(by_id) != set(message_ids):
            raise MemoryError("history_scope")
        result = []
        for message_id in message_ids:
            row = by_id[message_id]
            content = row["content"] if part == "question" else row["answer_text"] or ""
            if start_char and start_char >= len(content):
                raise MemoryError("history_scope")
            text = _clip(content[start_char:], 800)
            next_char = start_char + len(text)
            result.append(
                {
                    "message_id": message_id,
                    "run_id": row["id"],
                    "sequence": row["sequence"],
                    "snapshot_id": snapshot_id,
                    "part": part,
                    "text": text,
                    "is_historical": True,
                    "truncated": bool(start_char) or next_char < len(content),
                    "next_char": next_char if next_char < len(content) else None,
                }
            )
        return result
