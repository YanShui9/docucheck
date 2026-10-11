"""Pinned dynamic workflows and fenced, immutable extraction batch progress."""

import json
import re
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.exc import OperationalError

from ..criteria.models import ChecklistDefinition
from ..evidence.repository import digest
from .checklist_repository import ChecklistError, _version, ensure_legacy_run_allowed
from .item_binding_repository import (
    ItemBindingError,
    freeze_current,
    load_run_bindings,
    run_bindings_current,
)
from .models import (
    agent_runs,
    assistant_runs,
    cases,
    checklist_heads,
    dynamic_batches,
    dynamic_corrections,
    dynamic_runs,
    project_members,
)


def _error(code):
    from .run_repository import RepositoryError

    raise RepositoryError(code)


def source_digest(repo):
    return digest([asdict(b) for b in repo.blocks])


def bind_workflow(connection, request, run_id, *, item_binding_override=None):
    """Caller holds the Case lock; use current reads after prior RR selects."""
    head = (
        connection.execute(
            select(checklist_heads)
            .where(checklist_heads.c.case_id == request.case_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    workflow = request.workflow
    if workflow == "legacy" or (workflow == "auto" and head is None):
        try:
            ensure_legacy_run_allowed(connection, request.case_id)
        except ChecklistError as exc:
            _error(exc.code)
        return None
    if workflow not in {"auto", "proposal"}:
        _error("invalid_request")
    version = None
    if workflow == "auto":
        if (
            head is None
            or head["snapshot_id"] != request.snapshot_id
            or head["confirmed_version_id"] is None
            or (
                head["draft_json"] is not None
                and head["draft_base_version_id"] != head["confirmed_version_id"]
            )
        ):
            _error("checklist_confirmation_required")
        try:
            version = _version(
                connection,
                request.case_id,
                head["confirmed_version_id"],
                current_read=True,
            )
        except ChecklistError as exc:
            _error(exc.code)
        if version["snapshot_id"] != request.snapshot_id:
            _error("checklist_confirmation_required")
    try:
        frozen = (item_binding_override if item_binding_override is not None else
                  freeze_current(connection, request.case_id, request.snapshot_id,
                                 version["id"] if version else None, version["definition"] if version else None))
    except ItemBindingError as exc:
        _error(exc.code)
    return {
        "run_id": run_id,
        "case_id": request.case_id,
        "snapshot_id": request.snapshot_id,
        "workflow": "proposal" if workflow == "proposal" else "review",
        "checklist_revision": head["revision"] if head else 0,
        "checklist_version_id": version["id"] if version else None,
        "definition_digest": digest(version["definition"]) if version else None,
        "item_binding_version_id": frozen["version_id"],
        "item_binding_digest": frozen["digest"],
    }


class DynamicRepository:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def validate_binding(connection, binding):
        definition = None
        head = (
            connection.execute(
                select(checklist_heads)
                .where(checklist_heads.c.case_id == binding["case_id"])
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if (head["revision"] if head else 0) != binding["checklist_revision"]:
            _error("checklist_changed")
        if binding["workflow"] == "review":
            if (
                head is None
                or head["snapshot_id"] != binding["snapshot_id"]
                or head["confirmed_version_id"] != binding["checklist_version_id"]
            ):
                _error("checklist_changed")
            try:
                version = _version(
                    connection,
                    binding["case_id"],
                    binding["checklist_version_id"],
                    current_read=True,
                )
            except ChecklistError as exc:
                _error(exc.code)
            if digest(version["definition"]) != binding["definition_digest"]:
                _error("checklist_changed")
            definition = version["definition"]
        try:
            load_run_bindings(connection, binding, definition, current_read=True)
        except ItemBindingError as exc:
            _error(exc.code)

    def _guard(self, connection, claim):
        from .run_repository import RunRepository

        RunRepository(self.engine)._require_current_case(connection, claim.run_id)
        run = (
            connection.execute(
                select(agent_runs)
                .where(agent_runs.c.id == claim.run_id)
                .with_for_update()
            )
            .mappings()
            .one()
        )
        if (
            run["status"] != "running"
            or run["active_attempt_id"] != claim.active_attempt_id
            or run["fence"] != claim.fence
            or run["snapshot_id"] != claim.snapshot_id
        ):
            _error("stale_attempt")
        project = connection.scalar(
            select(cases.c.project_id).where(cases.c.id == run["case_id"])
        )
        if (
            project
            and connection.scalar(
                select(project_members.c.role)
                .where(
                    project_members.c.project_id == project,
                    project_members.c.principal_key == run["requester_key"],
                )
                .with_for_update()
            )
            is None
        ):
            _error("source_access_changed")
        binding = (
            connection.execute(
                select(dynamic_runs).where(dynamic_runs.c.run_id == claim.run_id)
            )
            .mappings()
            .first()
        )
        if (
            binding is None
            or binding["case_id"] != run["case_id"]
            or binding["snapshot_id"] != run["snapshot_id"]
        ):
            _error("dynamic_scope")
        self.validate_binding(connection, binding)
        return dict(binding)

    def binding(self, claim):
        with self.engine.begin() as connection:
            binding = self._guard(connection, claim)
            binding["definition"] = (
                _version(
                    connection,
                    binding["case_id"],
                    binding["checklist_version_id"],
                    current_read=True,
                )["definition"]
                if binding["checklist_version_id"]
                else None
            )
            binding["item_bindings"] = load_run_bindings(connection, binding, binding["definition"], current_read=True)
            return binding

    def validate(self, claim):
        with self.engine.begin() as connection:
            self._guard(connection, claim)

    def _manual_review_rows(self, claim):
        """Select bounded durable review state under the current Run's authority."""
        with self.engine.begin() as connection:
            binding = self._guard(connection, claim)
            conversation = connection.scalar(select(agent_runs.c.conversation_id).where(agent_runs.c.id == claim.run_id))
            rows = list(connection.execute(select(agent_runs.c.id, dynamic_runs.c.payload_digest)
                .join(dynamic_runs, dynamic_runs.c.run_id == agent_runs.c.id).where(
                    agent_runs.c.id != claim.run_id,
                    agent_runs.c.case_id == binding["case_id"],
                    agent_runs.c.conversation_id == conversation,
                    agent_runs.c.snapshot_id == binding["snapshot_id"],
                    agent_runs.c.status.in_(("completed", "partial")),
                    dynamic_runs.c.workflow == "review",
                    dynamic_runs.c.checklist_version_id == binding["checklist_version_id"],
                    dynamic_runs.c.item_binding_version_id == binding["item_binding_version_id"],
                    dynamic_runs.c.item_binding_digest == binding["item_binding_digest"],
                    dynamic_runs.c.payload_json["needs_review"].as_boolean().is_(True))
                .order_by(agent_runs.c.created_at.desc(), agent_runs.c.id.desc()).limit(33)).mappings())
        if len(rows) > 32:
            _error("dynamic_capacity")
        stamp = digest([conversation, {k: binding[k] for k in (
            "case_id", "snapshot_id", "checklist_version_id", "item_binding_version_id", "item_binding_digest")},
            [dict(row) for row in rows]])
        return rows, stamp

    def validate_manual_previous(self, claim, stamp):
        if self._manual_review_rows(claim)[1] != stamp:
            _error("dynamic_source_changed")

    def manual_previous(self, claim):
        """Use verified unresolved facts, independent from ordinary chat checkpoints."""
        from ..criteria.facts import (
            BINDING_RULE_VERSION,
            RULE_VERSION,
            FactCandidate,
            validate_fact,
        )

        rows, stamp = self._manual_review_rows(claim)
        latest, inherited = None, None
        for selected in rows:
            old, payload, repo = self.verified(selected["id"])
            if old["payload_digest"] != selected["payload_digest"] or not payload or not payload.get("needs_review"):
                _error("dynamic_source_changed")
            definition = ChecklistDefinition.model_validate(old["definition"])
            rule_version = BINDING_RULE_VERSION if old["item_bindings"] is not None else RULE_VERSION
            try:
                # Rebuild derived fields in the current rules before comparing
                # inheritance; historical grammar may produce different IDs.
                normalized = [validate_fact({k: f[k] for k in FactCandidate.model_fields}, definition,
                    repo, f["supplier"], rule_version=rule_version, item_bindings=old["item_bindings"])
                    for f in payload["facts"]]
            except ValueError:
                _error("dynamic_source_changed")
            facts = {digest({k: f[k] for k in FactCandidate.model_fields}) for f in normalized}
            if latest is None:
                latest, inherited = payload, facts
            elif not facts <= inherited:
                # Independent correction branches need reconciliation. Never
                # silently choose one or manufacture a merged prior report.
                _error("dynamic_source_changed")
        self.validate_manual_previous(claim, stamp)
        return latest, stamp

    def retain_cancelled_usage(self, claim, result, provider):
        from .models import run_attempts

        with self.engine.begin() as connection:
            run = (
                connection.execute(
                    select(agent_runs)
                    .where(agent_runs.c.id == claim.run_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if (
                run["status"] != "cancelled"
                or run["active_attempt_id"] != claim.active_attempt_id
                or run["fence"] != claim.fence
            ):
                return False
            connection.execute(
                update(run_attempts)
                .where(
                    run_attempts.c.id == claim.active_attempt_id,
                    run_attempts.c.run_id == claim.run_id,
                    run_attempts.c.status == "cancelled",
                )
                .values(
                    usage_json=result.get("usage"),
                    model_id=getattr(provider, "model_id", None),
                    outcome_unknown=any(
                        e.get("outcome_unknown") is True
                        for e in result.get("events", [])
                    ),
                )
            )
            return True

    def load_batch(self, claim, batch_id, input_digest):
        with self.engine.begin() as connection:
            self._guard(connection, claim)
            # The guard may establish an RR read view before waiting for the
            # Case lock. Read the batch committed by the preceding lock owner.
            row = (
                connection.execute(
                    select(dynamic_batches).where(
                        dynamic_batches.c.run_id == claim.run_id,
                        dynamic_batches.c.batch_id == batch_id,
                    ).with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            if (
                row["input_digest"] != input_digest
                or digest(row["output_json"]) != row["output_digest"]
            ):
                _error("batch_source_changed")
            return row["output_json"]

    def save_batch(self, claim, batch_id, input_digest, output):
        if (
            not all(
                isinstance(s, str) and re.fullmatch(r"[a-f0-9]{64}", s)
                for s in (batch_id, input_digest)
            )
            or not isinstance(output, dict)
            or len(json.dumps(output, ensure_ascii=False).encode()) > 128000
        ):
            _error("dynamic_capacity")
        for attempt in range(3):
            try:
                return self._save_batch(claim, batch_id, input_digest, output)
            except OperationalError as exc:
                # Missing-key current reads can deadlock across independent
                # Cases. Retry only MySQL's rolled-back transaction, never a
                # model request or an ambiguous connection/commit failure.
                if (self.engine.dialect.name != "mysql"
                        or getattr(exc.orig, "args", (None,))[0] != 1213
                        or attempt == 2):
                    raise

    def _save_batch(self, claim, batch_id, input_digest, output):
        with self.engine.begin() as connection:
            self._guard(connection, claim)
            # Use a current read after serialization, not the earlier RR view.
            existing = (
                connection.execute(
                    select(dynamic_batches).where(
                        dynamic_batches.c.run_id == claim.run_id,
                        dynamic_batches.c.batch_id == batch_id,
                    ).with_for_update()
                )
                .mappings()
                .first()
            )
            fingerprint = digest(output)
            if existing:
                if (
                    existing["input_digest"] != input_digest
                    or existing["output_digest"] != fingerprint
                ):
                    _error("batch_source_changed")
                return
            connection.execute(
                insert(dynamic_batches).values(
                    run_id=claim.run_id,
                    batch_id=batch_id,
                    input_digest=input_digest,
                    output_json=output,
                    output_digest=fingerprint,
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )

    def store_terminal(self, connection, run_id, payload):
        binding = (
            connection.execute(
                select(dynamic_runs).where(dynamic_runs.c.run_id == run_id)
            )
            .mappings()
            .first()
        )
        if binding is None:
            if payload is not None:
                _error("dynamic_scope")
            return
        self.validate_binding(connection, binding)
        if payload is None:
            return
        if (
            not isinstance(payload, dict)
            or len(json.dumps(payload, ensure_ascii=False).encode()) > 512000
        ):
            _error("dynamic_capacity")
        connection.execute(
            update(dynamic_runs)
            .where(dynamic_runs.c.run_id == run_id)
            .values(payload_json=payload, payload_digest=digest(payload))
        )

    def exists(self, run_id):
        with self.engine.connect() as connection:
            return (
                connection.scalar(
                    select(dynamic_runs.c.run_id).where(dynamic_runs.c.run_id == run_id)
                )
                is not None
            )

    def current(self, run_id):
        with self.engine.connect() as connection:
            binding = (
                connection.execute(
                    select(dynamic_runs).where(dynamic_runs.c.run_id == run_id)
                )
                .mappings()
                .first()
            )
            if binding is None:
                return True
            head = (
                connection.execute(
                    select(checklist_heads).where(
                        checklist_heads.c.case_id == binding["case_id"]
                    )
                )
                .mappings()
                .first()
            )
            if (head["revision"] if head else 0) != binding["checklist_revision"]:
                return False
            definition = _version(connection, binding["case_id"], binding["checklist_version_id"])["definition"] if binding["checklist_version_id"] else None
            return run_bindings_current(connection, binding, definition)

    def verified(self, run_id):
        try:
            return self._verified(run_id)
        except (ValueError, KeyError, TypeError):
            _error("dynamic_source_changed")

    def _verified(self, run_id):
        """Historical results use their original immutable definition; never promote stale ones."""
        from ..criteria.facts import (
            SUPPORTED_RULE_VERSIONS,
            FactCandidate,
            evaluate_checklist,
            validate_fact,
        )
        from ..criteria.validation import validate_definition
        from .agent_snapshot import load_agent_snapshot

        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        agent_runs,
                        dynamic_runs.c.workflow,
                        dynamic_runs.c.checklist_version_id,
                        dynamic_runs.c.checklist_revision,
                        dynamic_runs.c.definition_digest,
                        dynamic_runs.c.item_binding_version_id,
                        dynamic_runs.c.item_binding_digest,
                        dynamic_runs.c.payload_json,
                        dynamic_runs.c.payload_digest,
                        cases.c.revision.label("current_revision"),
                        cases.c.deletion_requested_at,
                    )
                    .join(dynamic_runs, dynamic_runs.c.run_id == agent_runs.c.id)
                    .join(cases, cases.c.id == agent_runs.c.case_id)
                    .where(agent_runs.c.id == run_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                _error("run_not_found")
            if row["deletion_requested_at"] is not None:
                _error("case_deleting")
            version = (
                _version(connection, row["case_id"], row["checklist_version_id"])
                if row["checklist_version_id"]
                else None
            )
            frozen = load_run_bindings(connection, row, version["definition"] if version else None)
        payload = row["payload_json"]
        if payload is None:
            return dict(row), None, None
        if digest(payload) != row["payload_digest"] or payload.get("version") != 1:
            _error("dynamic_source_changed")
        buyer_ids = None
        if row["workflow"] == "proposal":
            from .models import document_versions

            with self.engine.connect() as connection:
                buyer_ids = tuple(
                    connection.scalars(
                        select(document_versions.c.id).where(
                            document_versions.c.case_id == row["case_id"],
                            document_versions.c.snapshot_id == row["snapshot_id"],
                            document_versions.c.role == "requirement",
                        )
                    )
                )
            if not buyer_ids:
                _error("dynamic_source_changed")
        from .assistant_repository import AssistantRepository

        repo = load_agent_snapshot(
            self.engine, row["case_id"], row["snapshot_id"], document_ids=buyer_ids,
            allow_empty=AssistantRepository(self.engine).exists(run_id),
        )
        if payload.get("source_digest") != source_digest(repo):
            _error("dynamic_source_changed")
        if row["workflow"] == "proposal":
            if payload.get("definition"):
                definition = ChecklistDefinition.model_validate(payload["definition"])
                validate_definition(definition, repo)
            return dict(row), payload, repo
        definition = ChecklistDefinition.model_validate(version["definition"])
        from ..criteria.facts import BINDING_RULE_VERSIONS
        from ..criteria.item_bindings import review_bindings

        context = review_bindings(frozen, definition, repo) if frozen is not None else None
        if context is not None:
            if payload.get("rule_version") not in BINDING_RULE_VERSIONS or any(payload.get(k) != v for k, v in context.identity().items()):
                _error("dynamic_source_changed")
        elif payload.get("rule_version") in BINDING_RULE_VERSIONS:
            _error("dynamic_source_changed")
        if (
            digest(version["definition"]) != row["definition_digest"]
            or payload.get("definition") != version["definition"]
        ):
            _error("dynamic_source_changed")
        validate_definition(definition, repo)
        facts = []
        for encoded in payload.get("facts", []):
            candidate = FactCandidate.model_validate(
                {k: v for k, v in encoded.items() if k in FactCandidate.model_fields}
            )
            actual = validate_fact(candidate, definition, repo, candidate.supplier, rule_version=payload.get("rule_version"), item_bindings=context)
            if actual != encoded:
                _error("dynamic_source_changed")
            facts.append(actual)
        coverage = payload.get("coverage")
        if not isinstance(coverage, dict) or any(
            type(v) is not bool for v in coverage.values()
        ):
            _error("dynamic_scope")
        suppliers = {d.supplier for d in repo.documents if d.supplier}
        if set(coverage) != suppliers or len({f["fact_id"] for f in facts}) != len(
            facts
        ):
            _error("dynamic_scope")
        self._validate_manifest(row, payload, definition, repo, context=context)
        checks = evaluate_checklist(
            definition, facts, coverage, snapshot_id=repo.snapshot_id, rule_version=payload.get("rule_version"),
            item_coverage=payload.get("item_coverage"), item_bindings=context,
        )
        items = {i.item_id: i.name for i in definition.items}
        stored_checks = [
            {**c, "item_name": c.get("item_name", items.get(c.get("item_id")))}
            for c in payload.get("checks", [])
        ]
        if stored_checks != checks or payload.get("rule_version") not in SUPPORTED_RULE_VERSIONS:
            _error("dynamic_source_changed")
        return dict(row) | {"definition": version["definition"], "item_bindings": frozen}, payload, repo

    def _validate_manifest(self, row, payload, definition, repo, *, context=None):
        from ..agent.dynamic import review_plan
        from ..criteria.facts import validate_fact

        plans, source_complete = review_plan(repo, definition, rule_version=payload.get("rule_version"), item_bindings=context)
        expected = [
            entry for supplier in sorted(plans) for _, _, entry in plans[supplier]
        ]
        if payload.get("batch_manifest") != expected:
            # A stopped decision before extraction has no findings or claimed coverage.
            if (
                not payload.get("facts")
                and not any(payload["coverage"].values())
                and not payload.get("batch_manifest")
                and not payload.get("batch_coverage")
                and (context is None or not any(done for items in payload.get("item_coverage", {}).values() for done in items.values()))
            ):
                return
            _error("dynamic_source_changed")
        complete = {
            b["batch_id"]: b
            for b in payload.get("batch_coverage", [])
            if b.get("complete") is True
        }
        if not set(complete) <= {e["batch_id"] for e in expected}:
            _error("batch_source_changed")
        reported_facts = {f["fact_id"] for f in payload["facts"]}
        with self.engine.connect() as connection:
            rows = {
                r["batch_id"]: r
                for r in connection.execute(
                    select(dynamic_batches).where(dynamic_batches.c.run_id == row["id"])
                ).mappings()
            }
        for supplier, batches in plans.items():
            for batch, _, entry in batches:
                if entry["batch_id"] not in complete:
                    if (payload["item_coverage"][supplier][entry["item_id"]] if context else payload["coverage"][supplier]):
                        _error("dynamic_source_changed")
                    continue
                cached = rows.get(entry["batch_id"])
                if (
                    cached is None
                    or cached["input_digest"] != entry["input_digest"]
                    or digest(cached["output_json"]) != cached["output_digest"]
                    or complete[entry["batch_id"]]["input_digest"]
                    != entry["input_digest"]
                ):
                    _error("batch_source_changed")
                allowed = {b["evidence_id"] for b in batch}
                for candidate in cached["output_json"]["facts"]:
                    if (
                        not set(candidate["evidence_refs"]) <= allowed
                        or candidate["criterion_id"] not in entry["criterion_ids"]
                    ):
                        _error("batch_source_changed")
                    verified = validate_fact(candidate, definition, repo, supplier, rule_version=payload.get("rule_version"), item_bindings=context)
                    if verified["fact_id"] not in reported_facts:
                        _error("batch_source_changed")
            if context:
                if any(done and not source_complete[supplier][item] for item, done in payload["item_coverage"][supplier].items()):
                    _error("dynamic_source_changed")
            elif payload["coverage"][supplier] and not source_complete[supplier]:
                _error("dynamic_source_changed")

    def checks(self, run_id):
        row, payload, _ = self.verified(run_id)
        with self.engine.connect() as connection:
            live = (
                connection.execute(
                    select(
                        agent_runs.c.status,
                        agent_runs.c.snapshot_id,
                        cases.c.revision,
                        cases.c.deletion_requested_at,
                        dynamic_runs.c.payload_digest,
                    )
                    .join(cases, cases.c.id == agent_runs.c.case_id)
                    .join(dynamic_runs, dynamic_runs.c.run_id == agent_runs.c.id)
                    .where(agent_runs.c.id == run_id)
                )
                .mappings()
                .first()
            )
        if live is None or live["deletion_requested_at"] is not None:
            _error("case_deleting")
        if (
            live["payload_digest"] != row["payload_digest"]
            or live["status"] != row["status"]
            or live["snapshot_id"] != row["snapshot_id"]
        ):
            _error("run_changed")
        row["current_revision"] = live["revision"]
        current = row["revision"] == row["current_revision"] and self.current(run_id)
        result = {
            "run_id": run_id,
            "case_id": row["case_id"],
            "snapshot_id": row["snapshot_id"],
            "revision": row["revision"],
            "run_status": row["status"],
            "is_current": current,
            "checklist_version_id": row["checklist_version_id"],
            "checks": [],
            "payload_digest": row["payload_digest"],
        }
        if row["item_binding_digest"] is not None:
            result.update(item_binding_version_id=row["item_binding_version_id"], item_binding_digest=row["item_binding_digest"])
        if (
            payload
            and row["workflow"] == "review"
            and row["status"] in {"completed", "partial", "waiting_user"}
        ):
            result["checks"] = [
                {
                    **c,
                    "item_name": c.get(
                        "item_name",
                        next(
                            i["name"]
                            for i in row["definition"]["items"]
                            if i["item_id"] == c["item_id"]
                        ),
                    ),
                    "is_formal": bool(
                        current
                        and row["status"] == "completed"
                        and c["processing_status"] == "complete"
                        and not payload.get("needs_review")
                    ),
                    "checklist_version_id": row["checklist_version_id"],
                }
                for c in payload["checks"]
            ]
        return result

    def facts(self, run_id):
        row, payload, _ = self.verified(run_id)
        checks = self.checks(run_id)
        if checks["payload_digest"] != row["payload_digest"]:
            _error("run_changed")
        rules = {
            c["criterion_id"]: c["rule"]
            for c in row.get("definition", {}).get("criteria", [])
        }
        groups = {}
        for fact in (payload or {}).get("facts", []):
            key = (fact["supplier"], fact["criterion_id"])
            groups.setdefault(key, []).append(
                {**fact, "raw_value": fact["value"], "raw_unit": fact["unit"]}
            )
        result = {
            "run_id": run_id,
            "case_id": row["case_id"],
            "snapshot_id": row["snapshot_id"],
            "revision": row["revision"],
            "is_current": checks["is_current"],
            "available": payload is not None,
            "checklist_version_id": row["checklist_version_id"],
            "payload_digest": row["payload_digest"],
            "fact_sets": [
                {
                    "fact_set_id": digest([run_id, *key]),
                    "supplier": key[0],
                    "field_key": key[1],
                    "criterion_id": key[1],
                    "rule": rules[key[1]],
                    "facts": facts,
                }
                for key, facts in groups.items()
            ],
        }
        if row["item_binding_digest"] is not None:
            result.update(item_binding_version_id=row["item_binding_version_id"], item_binding_digest=row["item_binding_digest"])
        return result

    def proposal(self, run_id):
        row, payload, _ = self.verified(run_id)
        if row["workflow"] != "proposal":
            _error("dynamic_scope")
        definition = (
            ChecklistDefinition.model_validate(payload["definition"])
            if payload and payload.get("definition")
            else None
        )
        return {
            "run_id": run_id,
            "case_id": row["case_id"],
            "snapshot_id": row["snapshot_id"],
            "revision": row["revision"],
            "checklist_revision": row["checklist_revision"],
            "is_current": row["revision"] == row["current_revision"]
            and self.current(run_id),
            "run_status": row["status"],
            "definition": definition.model_dump(mode="json") if definition else None,
            "issues": definition.issues() if definition else [],
            "requires_user_confirmation": True,
        }

    def progress(self, run_id):
        row, payload, _ = self.verified(run_id)
        # A Run may retain cache rows from earlier rule versions. Count only
        # completed batches in the verified current report's manifest coverage.
        batches = {entry['batch_id'] for entry in (payload or {}).get('batch_coverage', [])
                   if entry.get('complete') is True}
        result = {
            "run_id": run_id,
            "workflow": row["workflow"],
            "run_status": row["status"],
            "checklist_version_id": row["checklist_version_id"],
            "validated_batches": len(batches),
            "planned_batches": len((payload or {}).get("batch_manifest", [])),
            "coverage": (payload or {}).get("coverage", {}),
        }
        if row["item_binding_digest"] is not None:
            result.update(item_binding_version_id=row["item_binding_version_id"], item_binding_digest=row["item_binding_digest"],
                          item_coverage=(payload or {}).get("item_coverage", {}))
        return result

    def correction(self, run_id, body, *, actor, key):
        """Append source-supported alternatives; never overwrite originals or auto resolve."""
        from ..criteria.facts import FactCandidate, evaluate_checklist, validate_fact
        from .assistant_repository import AssistantRepository
        from .checklist_repository import _scope
        from .memory_repository import save_checkpoint

        fingerprint = digest(body)
        # Fast read before the write transaction; its digest is compared under locks.
        row, payload, repo = self.verified(run_id)
        if row["workflow"] != "review" or payload is None:
            _error("facts_unavailable")
        assistant = AssistantRepository(self.engine)
        assistant_row, assistant_payload, _ = (
            assistant.verified(run_id) if assistant.exists(run_id) else (None, None, None)
        )
        if assistant_row is not None and (
            assistant_payload is None or assistant_payload["kind"] != "review"
            or assistant_payload["dynamic_payload_digest"] != row["payload_digest"]
        ):
            _error("assistant_source_changed")
        candidate = FactCandidate.model_validate(body["candidate"])
        old = next(
            (f for f in payload["facts"] if f["fact_id"] == body["fact_id"]), None
        )
        with self.engine.begin() as connection:
            _scope(
                connection,
                row["case_id"],
                row["snapshot_id"],
                expected_revision=body["expected_revision"],
                actor=actor,
            )
            prior = (
                connection.execute(
                    select(dynamic_corrections).where(
                        dynamic_corrections.c.run_id == run_id,
                        dynamic_corrections.c.principal_key == actor,
                        dynamic_corrections.c.idempotency_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if prior:
                if prior["request_digest"] != fingerprint:
                    _error("idempotency_conflict")
                return {
                    "correction_id": prior["id"],
                    "run_id": run_id,
                    "review_status": "needs_review",
                    "payload_digest": digest(prior["new_payload_json"]),
                }
            binding = (
                connection.execute(
                    select(dynamic_runs)
                    .where(dynamic_runs.c.run_id == run_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            self.validate_binding(connection, binding)
            if not run_bindings_current(connection, binding, row["definition"]):
                _error("dynamic_source_changed")
            if (
                binding["payload_digest"] != row["payload_digest"]
                or body["expected_payload_digest"] != row["payload_digest"]
            ):
                _error("run_changed")
            run = (
                connection.execute(
                    select(agent_runs)
                    .where(agent_runs.c.id == run_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if run["status"] not in {"completed", "partial"}:
                _error("invalid_transition")
            if connection.scalar(
                select(agent_runs.c.id)
                .where(
                    agent_runs.c.case_id == row["case_id"],
                    agent_runs.c.status.in_(("queued", "running", "waiting_user")),
                )
                .limit(1)
                .with_for_update()
            ):
                _error("case_busy")
            if old is None:
                _error("fact_not_found")
            if any(
                getattr(candidate, k) != old[k]
                for k in (
                    "supplier",
                    "criterion_id",
                    "item_id",
                    "evidence_refs",
                    "quote",
                )
            ):
                _error("unsupported_correction")
            try:
                definition = ChecklistDefinition.model_validate(payload["definition"])
                corrected = validate_fact(
                    candidate, definition, repo, candidate.supplier, rule_version=payload.get("rule_version"), item_bindings=row.get("item_bindings")
                )
            except ValueError:
                _error("unsupported_correction")
            if corrected["fact_id"] in {f["fact_id"] for f in payload["facts"]}:
                _error("correction_unchanged")
            changed = deepcopy(payload)
            changed["facts"].append(corrected)
            changed["needs_review"] = True
            changed["checks"] = evaluate_checklist(
                definition,
                changed["facts"],
                changed["coverage"],
                snapshot_id=row["snapshot_id"],
                rule_version=payload.get("rule_version"),
                item_coverage=payload.get("item_coverage"), item_bindings=row.get("item_bindings"),
            )
            if len(json.dumps(changed, ensure_ascii=False).encode()) > 512000:
                _error("dynamic_capacity")
            correction_id, now = uuid4().hex, datetime.now(UTC).replace(tzinfo=None)
            connection.execute(
                insert(dynamic_corrections).values(
                    id=correction_id,
                    run_id=run_id,
                    principal_key=actor,
                    idempotency_key=key,
                    request_digest=fingerprint,
                    # Audit-only metadata; the original Run payload is unchanged.
                    old_payload_json=payload | {"correction_target_fact_id": old["fact_id"]},
                    new_payload_json=changed,
                    reason=body["reason"],
                    created_at=now,
                )
            )
            connection.execute(
                update(dynamic_runs)
                .where(dynamic_runs.c.run_id == run_id)
                .values(payload_json=changed, payload_digest=digest(changed))
            )
            checkpoint = dict(run["memory_json"] or {}) | {
                "payload_digest": digest(changed),
                "source_run_id": run_id,
            }
            from ..agent.dynamic import render_review

            answer = render_review(
                changed["checks"], complete=all(changed["coverage"].values()),
                needs_review=True, repo=repo,
            )
            if assistant_row is not None:
                live_assistant = connection.execute(select(assistant_runs).where(
                    assistant_runs.c.run_id == run_id).with_for_update()).mappings().one()
                if any(live_assistant[k] != assistant_row[k] for k in (
                    "payload_digest", "base_digest", "payload_json", "base_json",
                )):
                    _error("run_changed")
                updated_assistant = assistant_payload | {
                    "answer": answer, "dynamic_payload_digest": digest(changed),
                }
                connection.execute(update(assistant_runs).where(
                    assistant_runs.c.run_id == run_id).values(
                        payload_json=updated_assistant, payload_digest=digest(updated_assistant)))
                # Version 5 checkpoints refer to the assistant envelope, not its nested report.
                checkpoint["payload_digest"] = digest(updated_assistant)
            connection.execute(
                update(agent_runs)
                .where(agent_runs.c.id == run_id)
                .values(
                    memory_json=checkpoint,
                    answer_text=answer,
                    updated_at=now,
                )
            )
            save_checkpoint(connection, run_id, now=now)
            return {
                "correction_id": correction_id,
                "run_id": run_id,
                "review_status": "needs_review",
                "payload_digest": digest(changed),
            }

    def corrections(self, run_id):
        verified_row, _, _ = self.verified(run_id)
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(dynamic_corrections)
                    .where(dynamic_corrections.c.run_id == run_id)
                    .order_by(
                        dynamic_corrections.c.created_at, dynamic_corrections.c.id
                    )
                )
                .mappings()
                .all()
            )
        output = []
        for row in rows:
            old_facts = row["old_payload_json"]["facts"]
            new_fact = next(
                f for f in row["new_payload_json"]["facts"] if f not in old_facts
            )
            matches = [
                f
                for f in old_facts
                if all(
                    f[k] == new_fact[k]
                    for k in (
                        "criterion_id",
                        "item_id",
                        "supplier",
                        "evidence_refs",
                        "quote",
                    )
                )
            ]
            target = row["old_payload_json"].get("correction_target_fact_id")
            # Legacy audits did not store the chosen ID. Never guess among alternatives.
            old = (
                next((f for f in matches if f["fact_id"] == target), None)
                if target else matches[0] if len(matches) == 1 else None
            )
            output.append(
                {
                    "correction_id": row["id"],
                    "reason": row["reason"],
                    "old_fact": old,
                    "corrected_fact": new_fact,
                    "review_status": "needs_review",
                }
            )
        latest, _, _ = self.verified(run_id)
        if latest["payload_digest"] != verified_row["payload_digest"]:
            _error("run_changed")
        return {
            "run_id": run_id,
            "payload_digest": verified_row["payload_digest"],
            "corrections": output,
        }
