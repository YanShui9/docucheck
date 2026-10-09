"""Immutable preview lineage, separate from the saved checklist authority."""

from sqlalchemy import select

from ..criteria.changes import apply_changes
from ..criteria.models import ChecklistDefinition
from ..evidence.repository import digest
from .dynamic_repository import _error
from .models import agent_runs, assistant_runs, messages, run_attempts

PREVIEW_KIND = "assistant_preview_v1"
MAX_PREVIEW_DEPTH = 16
_SCOPE = ("case_id", "conversation_id", "snapshot_id", "checklist_revision", "checklist_version_id")


def authority_base(value):
    if isinstance(value, dict) and value.get("kind") == PREVIEW_KIND:
        if set(value) != {"kind", "authority", "parent"}:
            _error("assistant_source_changed")
        return value["authority"]
    return value


def _row_query():
    return select(agent_runs, assistant_runs.c.run_id, assistant_runs.c.checklist_revision,
        assistant_runs.c.checklist_version_id, assistant_runs.c.base_json, assistant_runs.c.base_digest,
        assistant_runs.c.payload_json, assistant_runs.c.payload_digest, messages.c.sequence.label("message_sequence"))\
        .join(assistant_runs, assistant_runs.c.run_id == agent_runs.c.id)\
        .join(messages, messages.c.id == agent_runs.c.message_id)


def _read(statement, current_read):
    return statement.with_for_update() if current_read else statement


def _candidate(connection, reference, scope, authority, before_sequence, seen, source=None, *, current_read=False):
    """Replay, rather than trust a stored candidate even with a recomputed digest."""
    from ..agent.assistant import AssistantPayload

    if not isinstance(reference, dict) or set(reference) != {"run_id", "payload_digest"}:
        _error("assistant_source_changed")
    run_id = reference["run_id"]
    if not isinstance(run_id, str) or run_id in seen:
        _error("assistant_source_changed")
    if len(seen) >= MAX_PREVIEW_DEPTH:
        _error("assistant_preview_limit")
    seen.add(run_id)
    row = connection.execute(_read(_row_query().where(agent_runs.c.id == run_id), current_read)).mappings().first()
    if (row is None or row["status"] != "completed" or any(row[key] != scope[key] for key in _SCOPE)
            or (before_sequence is not None and row["message_sequence"] >= before_sequence)):
        _error("assistant_source_changed")
    payload = row["payload_json"]
    if (digest(row["base_json"]) != row["base_digest"] or digest(authority_base(row["base_json"])) != digest(authority)
            or payload is None or digest(payload) != reference["payload_digest"]
            or row["payload_digest"] != reference["payload_digest"]):
        _error("assistant_source_changed")
    parsed = AssistantPayload.model_validate(payload)
    if (parsed.kind != "changes" or parsed.answer != row["answer_text"]
            or (source is not None and parsed.source_digest != source)):
        _error("assistant_source_changed")
    reply = connection.scalar(_read(select(run_attempts.c.resume_message_id).where(
        run_attempts.c.run_id == run_id, run_attempts.c.resume_message_id.is_not(None))
        .order_by(run_attempts.c.attempt_no.desc()).limit(1), current_read))
    instruction = connection.scalar(_read(select(messages.c.content).where(
        messages.c.id == (reply or row["message_id"])), current_read))
    if instruction != parsed.instruction:
        _error("assistant_source_changed")
    base = row["base_json"]
    if isinstance(base, dict) and base.get("kind") == PREVIEW_KIND:
        base = _candidate(connection, base["parent"], scope, authority, row["message_sequence"], seen,
                          parsed.source_digest, current_read=current_read)
    candidate, changes = apply_changes(ChecklistDefinition.model_validate(base) if base else None, parsed.patch, instruction)
    definition = candidate.model_dump(mode="json")
    if definition != payload["candidate"] or changes != payload["changes"]:
        _error("assistant_source_changed")
    return definition


def bind_preview(connection, request, authority, revision, version):
    """Freeze the latest completed changes in this exact conversation and scope."""
    scope = {"case_id": request.case_id, "conversation_id": request.conversation_id,
             "snapshot_id": request.snapshot_id, "checklist_revision": revision, "checklist_version_id": version}
    row = connection.execute(_row_query().where(
        agent_runs.c.case_id == request.case_id, agent_runs.c.conversation_id == request.conversation_id,
        agent_runs.c.snapshot_id == request.snapshot_id, agent_runs.c.status == "completed",
        assistant_runs.c.checklist_revision == revision, assistant_runs.c.checklist_version_id == version,
        assistant_runs.c.payload_json["kind"].as_string() == "changes")
        .order_by(messages.c.sequence.desc()).limit(1).with_for_update()).mappings().first()
    if row is None:
        return authority
    reference = {"run_id": row["id"], "payload_digest": row["payload_digest"]}
    seen = set()
    try:
        _candidate(connection, reference, scope, authority, None, seen, current_read=True)
    except (ValueError, KeyError, TypeError):
        _error("assistant_source_changed")
    if len(seen) >= MAX_PREVIEW_DEPTH:
        _error("assistant_preview_limit")
    return {"kind": PREVIEW_KIND, "authority": authority, "parent": reference}


def effective_base(connection, binding, *, source=None, current_read=False):
    base = binding["base_json"]
    if digest(base) != binding["base_digest"]:
        _error("assistant_source_changed")
    authority = authority_base(base)
    if authority is base:
        return base
    owner = connection.execute(_read(select(agent_runs.c.conversation_id, messages.c.sequence)
        .join(messages, messages.c.id == agent_runs.c.message_id)
        .where(agent_runs.c.id == binding["run_id"]), current_read)).mappings().one()
    scope = dict(binding) | {"conversation_id": owner["conversation_id"]}
    try:
        return _candidate(connection, base["parent"], scope, authority, owner["sequence"], set(), source,
                          current_read=current_read)
    except (ValueError, KeyError, TypeError):
        _error("assistant_source_changed")
