"""Fixed edit bases, fenced assistant payloads, and historical source verification."""

import json

from sqlalchemy import insert, select, update

from ..criteria.changes import apply_changes
from ..criteria.models import ChecklistDefinition
from ..criteria.validation import validate_definition
from ..evidence.repository import digest
from .assistant_preview import authority_base, bind_preview, effective_base
from .checklist_repository import _validated, _version
from .dynamic_repository import DynamicRepository, _error, bind_workflow, source_digest
from .models import (
    agent_runs,
    assistant_runs,
    cases,
    checklist_heads,
    dynamic_runs,
    messages,
    run_attempts,
)


def _base(connection, case_id, snapshot_id):
    head = connection.execute(select(checklist_heads).where(checklist_heads.c.case_id == case_id).with_for_update()).mappings().first()
    base = None
    if head and head["snapshot_id"] == snapshot_id:
        confirmed = (_version(connection, case_id, head["confirmed_version_id"], current_read=True)
                     if head["confirmed_version_id"] else None)
        if head["draft_json"] is not None:
            draft = _validated(connection, case_id, snapshot_id, head["draft_json"],
                               head["source_bindings_json"], head["draft_digest"], current_read=True).model_dump(mode="json")
            if confirmed and head["draft_base_version_id"] == head["confirmed_version_id"]:
                if confirmed["snapshot_id"] != snapshot_id:
                    _error("checklist_changed")
                items = {i["item_id"]: i for i in confirmed["definition"]["items"]}
                items.update({i["item_id"]: i for i in draft["items"]})
                criteria = {c["criterion_id"]: c for c in confirmed["definition"]["criteria"]}
                criteria.update({c["criterion_id"]: c for c in draft["criteria"]})
                base = {"items": list(items.values()), "criteria": list(criteria.values())}
            else:
                base = draft
        elif confirmed and confirmed["snapshot_id"] == snapshot_id:
            base = confirmed["definition"]
    return head, base


def bind_assistant(connection, request, run_id):
    head, base = _base(connection, request.case_id, request.snapshot_id)
    if base:
        base = ChecklistDefinition.model_validate(base).model_dump(mode="json")
    base = bind_preview(connection, request, base, head["revision"] if head else 0,
                        head["confirmed_version_id"] if head else None)
    return {"run_id": run_id, "case_id": request.case_id, "snapshot_id": request.snapshot_id,
            "checklist_revision": head["revision"] if head else 0,
            "checklist_version_id": head["confirmed_version_id"] if head else None,
            "base_json": base, "base_digest": digest(base)}


class AssistantRepository:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def validate_binding(connection, binding, *, source=None):
        head, base = _base(connection, binding["case_id"], binding["snapshot_id"])
        if (head["revision"] if head else 0) != binding["checklist_revision"] or (head["confirmed_version_id"] if head else None) != binding["checklist_version_id"]:
            _error("checklist_changed")
        if base is not None:
            base = ChecklistDefinition.model_validate(base).model_dump(mode="json")
        if digest(binding["base_json"]) != binding["base_digest"] or digest(base) != digest(authority_base(binding["base_json"])):
            _error("assistant_source_changed")
        return effective_base(connection, binding, source=source, current_read=True)

    def exists(self, run_id):
        with self.engine.connect() as connection:
            return connection.scalar(select(assistant_runs.c.run_id).where(assistant_runs.c.run_id == run_id)) is not None

    def _guard(self, connection, claim, *, source=None):
        from .run_repository import RunRepository

        runs = RunRepository(self.engine)
        runs._require_current_case(connection, claim.run_id)
        run = connection.execute(select(agent_runs).where(agent_runs.c.id == claim.run_id).with_for_update()).mappings().one()
        if run["status"] != "running" or run["active_attempt_id"] != claim.active_attempt_id or run["fence"] != claim.fence or run["snapshot_id"] != claim.snapshot_id:
            _error("stale_attempt")
        row = connection.execute(select(assistant_runs).where(assistant_runs.c.run_id == claim.run_id).with_for_update()).mappings().first()
        if row is None or row["case_id"] != run["case_id"] or row["snapshot_id"] != run["snapshot_id"]:
            _error("assistant_scope")
        definition = self.validate_binding(connection, row, source=source)
        return dict(row) | ({"effective_definition": definition} if source is not None else {})

    def binding(self, claim, *, source=None):
        with self.engine.begin() as connection:
            return self._guard(connection, claim, source=source)

    def validate(self, claim):
        self.binding(claim)

    def register_formal(self, claim, operation):
        """The executor owns routing to dynamic workflows; no model binding is accepted."""
        from dataclasses import replace

        from .run_repository import SubmitRun

        with self.engine.begin() as connection:
            self._guard(connection, claim)
            run = connection.execute(select(agent_runs).where(agent_runs.c.id == claim.run_id).with_for_update()).mappings().one()
            if run["revision"] < 1 and operation != "review":
                _error("snapshot_not_ready")
            request = SubmitRun(run["case_id"], run["conversation_id"], run["snapshot_id"], run["revision"], "formal", run["mode"], "internal")
            request = replace(request, workflow="proposal" if operation == "proposal" else "auto")
            binding = bind_workflow(connection, request, claim.run_id)
            if binding is None:
                _error("checklist_confirmation_required")
            old = connection.execute(select(dynamic_runs).where(dynamic_runs.c.run_id == claim.run_id).with_for_update()).mappings().first()
            if old is None:
                connection.execute(insert(dynamic_runs).values(**binding))
            elif any(old[k] != v for k, v in binding.items()):
                _error("dynamic_scope")
        return DynamicRepository(self.engine).binding(claim)

    def store_terminal(self, connection, run_id, payload):
        binding = connection.execute(select(assistant_runs).where(assistant_runs.c.run_id == run_id).with_for_update()).mappings().first()
        if binding is None:
            if payload is not None:
                _error("assistant_scope")
            return
        self.validate_binding(connection, binding)
        if payload is not None:
            if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False).encode()) > 512000:
                _error("assistant_capacity")
            from ..agent.assistant import AssistantPayload

            try:
                AssistantPayload.model_validate(payload)
            except (ValueError, TypeError):
                _error("assistant_source_changed")
            if payload.get("kind") not in {"review", "proposal"}:
                # A retry may choose a different assistant intent. Never pair
                # its new completed status with the previous attempt's facts.
                connection.execute(update(dynamic_runs).where(dynamic_runs.c.run_id == run_id).values(payload_json=None, payload_digest=None))
            connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == run_id).values(payload_json=payload, payload_digest=digest(payload)))

    def current(self, run_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(assistant_runs).where(assistant_runs.c.run_id == run_id)).mappings().first()
            if row is None:
                return True
            head = connection.execute(select(checklist_heads).where(checklist_heads.c.case_id == row["case_id"])).mappings().first()
            return (head["revision"] if head else 0) == row["checklist_revision"]

    def verified(self, run_id):
        from .run_repository import RepositoryError

        try:
            return self._verified(run_id)
        except RepositoryError:
            raise
        except (ValueError, KeyError, TypeError):
            _error("assistant_source_changed")

    def _verified(self, run_id):
        from ..agent.assistant import (
            AssistantPayload,
            checked_citation,
            render_evidence,
        )
        from ..agent.assistant_evidence import verify_windows
        from .agent_snapshot import load_agent_snapshot

        with self.engine.connect() as connection:
            row = connection.execute(select(agent_runs, assistant_runs.c.checklist_revision,
                assistant_runs.c.checklist_version_id, assistant_runs.c.base_json, assistant_runs.c.base_digest,
                assistant_runs.c.payload_json, assistant_runs.c.payload_digest,
                cases.c.revision.label("current_revision"), cases.c.deletion_requested_at)
                .join(assistant_runs, assistant_runs.c.run_id == agent_runs.c.id).join(cases, cases.c.id == agent_runs.c.case_id)
                .where(agent_runs.c.id == run_id)).mappings().first()
            if row is None:
                _error("run_not_found")
            if row["deletion_requested_at"] is not None:
                _error("case_deleting")
            payload = row["payload_json"]
            if digest(row["base_json"]) != row["base_digest"]:
                _error("assistant_source_changed")
            if payload is None:
                return dict(row), None, None
            if digest(payload) != row["payload_digest"] or payload["version"] != 1 or payload["answer"] != row["answer_text"]:
                _error("assistant_source_changed")
            AssistantPayload.model_validate(payload)
            reply = connection.scalar(select(run_attempts.c.resume_message_id).where(run_attempts.c.run_id == run_id, run_attempts.c.resume_message_id.is_not(None)).order_by(run_attempts.c.attempt_no.desc()).limit(1))
            instruction = connection.scalar(select(messages.c.content).where(messages.c.id == (reply or row["message_id"])))
            if instruction != payload["instruction"]:
                _error("assistant_source_changed")
            edit_base = effective_base(connection, dict(row) | {"run_id": run_id}, source=payload["source_digest"])
        repo = load_agent_snapshot(self.engine, row["case_id"], row["snapshot_id"], allow_empty=True)
        if payload["source_digest"] != source_digest(repo):
            _error("assistant_source_changed")
        base = ChecklistDefinition.model_validate(edit_base) if edit_base else None
        if base:
            validate_definition(base, repo)
        kind = payload["kind"]
        if kind == "changes":
            candidate, changes = apply_changes(base, payload["patch"], instruction)
            validate_definition(candidate, repo)
            if candidate.model_dump(mode="json") != payload["candidate"] or changes != payload["changes"]:
                _error("assistant_source_changed")
        elif kind == "evidence":
            verify_windows(repo, payload)
            citations = [checked_citation(repo, c["evidence_id"], c["quote"]) for c in payload["citations"]]
            if citations != payload["citations"] or render_evidence(citations, payload["retrieval"]) != payload["answer"]:
                _error("assistant_source_changed")
        elif kind in {"review", "proposal"}:
            dynamic_row, dynamic_payload, _ = DynamicRepository(self.engine).verified(run_id)
            if dynamic_row["workflow"] != kind or digest(dynamic_payload) != payload["dynamic_payload_digest"]:
                _error("assistant_source_changed")
            if kind == "proposal" and payload["candidate"] != (dynamic_payload or {}).get("definition"):
                _error("assistant_source_changed")
        elif kind != "chat":
            _error("assistant_source_changed")
        if kind in {"chat", "evidence"} and (payload["candidate"] is not None or payload["changes"] or payload["patch"] is not None):
            _error("assistant_source_changed")
        if kind != "evidence" and payload["citations"]:
            _error("assistant_source_changed")
        # Recheck after potentially expensive source validation, including deletion.
        with self.engine.connect() as connection:
            live = connection.execute(select(agent_runs.c.status, agent_runs.c.answer_text,
                assistant_runs.c.payload_digest, cases.c.revision, cases.c.deletion_requested_at)
                .join(assistant_runs, assistant_runs.c.run_id == agent_runs.c.id).join(cases, cases.c.id == agent_runs.c.case_id)
                .where(agent_runs.c.id == run_id)).mappings().first()
        if live is None or live["deletion_requested_at"] is not None:
            _error("case_deleting")
        if live["status"] != row["status"] or live["answer_text"] != row["answer_text"] or live["payload_digest"] != row["payload_digest"]:
            _error("assistant_source_changed")
        return dict(row) | {"current_revision": live["revision"]}, payload, repo

    def result(self, run_id):
        row, payload, _ = self.verified(run_id)
        return {"run_id": run_id, "case_id": row["case_id"], "snapshot_id": row["snapshot_id"],
                "revision": row["revision"], "workflow": "assistant", "run_status": row["status"],
                "is_current": row["revision"] == row["current_revision"] and self.current(run_id),
                "base_digest": row["base_digest"], "checklist_revision": row["checklist_revision"],
                "checklist_version_id": row["checklist_version_id"], "payload_digest": row["payload_digest"], "result": payload}

    def proposal(self, run_id):
        result = self.result(run_id)
        payload = result["result"]
        if payload is None or payload["kind"] != "changes":
            _error("assistant_scope")
        return {k: v for k, v in result.items() if k != "result"} | {
            "definition": payload["candidate"], "changes": payload["changes"],
            "requires_user_confirmation": True, "instruction": payload["instruction"]}

    def checks(self, run_id):
        result = self.result(run_id)
        return {k: result[k] for k in ("run_id", "case_id", "snapshot_id", "revision", "run_status", "is_current")} | {"checks": []}

    def facts(self, run_id):
        result = self.result(run_id)
        return {k: result[k] for k in ("run_id", "case_id", "snapshot_id", "revision", "is_current")} | {"available": False, "fact_sets": []}
