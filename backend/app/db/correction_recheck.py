"""Revalidate a corrected source fact and run the existing deterministic rule."""

import re
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import IntegrityError

from ..agent.fact_memory import decode_fact_sets, encode_fact_sets
from ..evidence.repository import digest
from ..extraction.fields import FieldCandidate, _validate_candidate
from ..extraction.memory import FactSet
from ..extraction.validation import Candidate, validate_memory
from ..field_contract import FieldFactSet
from ..rules.fields import evaluate_field
from ..rules.memory import evaluate_memory
from .agent_snapshot import SnapshotLoadError, load_agent_snapshot
from .correction_repository import (
    CorrectionError,
    CorrectionRepository,
    SubmitCorrection,
    _plain,
    _review_fingerprint,
)
from .models import agent_runs, cases, correction_reviews, corrections, input_snapshots

_CAPACITY = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(MiB|GiB|GB)\b", re.IGNORECASE)


def _matching_documents(old_repo, new_repo, document_map):
    old_docs = {document.document_id: document for document in old_repo.documents}
    new_docs = {document.document_id: document for document in new_repo.documents}
    if set(document_map) != set(old_docs) or set(document_map.values()) != set(new_docs):
        raise CorrectionError("correction_source_changed")
    for old_id, new_id in document_map.items():
        old, new = old_docs[old_id], new_docs[new_id]
        if (old.source_hash, old.role, old.supplier, old.model,
            old.parsed.parser_version, old.parsed.status,
            old.parsed.coverage_issues, old.parsed.total_pages,
            old.parsed.processed_pages, old.parsed.total_sheets,
            old.parsed.processed_sheets, old_repo.source_name(old_id)) != (
            new.source_hash, new.role, new.supplier, new.model,
            new.parsed.parser_version, new.parsed.status,
            new.parsed.coverage_issues, new.parsed.total_pages,
            new.parsed.processed_pages, new.parsed.total_sheets,
            new.parsed.processed_sheets, new_repo.source_name(new_id)):
            raise CorrectionError("correction_source_changed")


def _evidence_map(old_repo, new_repo):
    if len(old_repo.blocks) != len(new_repo.blocks):
        raise CorrectionError("correction_source_changed")
    new_blocks = {block.evidence_id: block for block in new_repo.blocks}
    document_map = {}
    result = {}
    for old in old_repo.blocks:
        new_id = digest([new_repo.snapshot_id, old.evidence_id])
        new = new_blocks.get(new_id)
        if new is None:
            raise CorrectionError("correction_source_changed")
        old_content = {key: value for key, value in asdict(old).items()
                       if key not in {"evidence_id", "document_id"}}
        new_content = {key: value for key, value in asdict(new).items()
                       if key not in {"evidence_id", "document_id"}}
        if old_content != new_content:
            raise CorrectionError("correction_source_changed")
        existing = document_map.setdefault(old.document_id, new.document_id)
        if existing != new.document_id:
            raise CorrectionError("correction_source_changed")
        result[old.evidence_id] = new_id
    if len(set(document_map.values())) != len(document_map):
        raise CorrectionError("correction_source_changed")
    _matching_documents(old_repo, new_repo, document_map)
    return result


def _memory_rebind(old, refs, new_repo, supplier):
    text = " ".join(block.text for block in new_repo.read(list(refs)))
    for raw, unit in _CAPACITY.findall(text):
        try:
            candidate = Candidate(
                raw_value=raw, raw_unit={"gb": "GB", "gib": "GiB", "mib": "MiB"}[unit.casefold()],
                qualifier=old.qualifier, comparator=old.comparator,
                evidence_refs=list(refs),
            )
            fact = validate_memory(candidate, new_repo, supplier)
            if (fact.value, fact.unit, fact.qualifier, fact.comparator,
                fact.role, fact.supplier, fact.model) == (
                old.value, old.unit, old.qualifier, old.comparator,
                old.role, old.supplier, old.model):
                return fact
        except (TypeError, ValueError):
            continue
    raise CorrectionError("correction_source_changed")


def _rebind(old, refs, new_repo, supplier):
    if hasattr(old, "field_key"):
        try:
            fact = _validate_candidate(FieldCandidate(
                field_key=old.field_key, raw_value=old.raw_value,
                raw_unit=old.raw_unit, evidence_refs=list(refs),
            ), new_repo, supplier)
        except (TypeError, ValueError) as exc:
            raise CorrectionError("correction_source_changed") from exc
        stable = ("field_key", "raw_value", "raw_unit", "value", "unit",
                  "qualifier", "comparator", "role", "supplier", "model", "attributes")
        if any(getattr(fact, key) != getattr(old, key) for key in stable):
            raise CorrectionError("correction_source_changed")
        return fact
    return _memory_rebind(old, refs, new_repo, supplier)


def _prepared(correction, source_run, old_repo, sets, new_repo):
    if (source_run["case_id"] != correction["case_id"]
            or source_run["snapshot_id"] != correction["from_snapshot_id"]
            or source_run["status"] not in {"completed", "partial"}
            or new_repo.revision != source_run["revision"] + 1):
        raise CorrectionError("correction_source_changed")
    fact_set = sets.get(correction["fact_set_id"])
    if fact_set is None or fact_set.supplier != correction["supplier"]:
        raise CorrectionError("correction_source_changed")
    old = next((fact for fact in fact_set.facts
                if fact.fact_id == correction["fact_id"]), None)
    if old is None or _plain(asdict(old)) != correction["old_fact_json"]:
        raise CorrectionError("correction_source_changed")
    if (correction["field_key"] != (fact_set.field_key.value if isinstance(
            fact_set, FieldFactSet) else "memory")
            or list(old.evidence_refs) != correction["source_evidence_ids_json"]):
        raise CorrectionError("correction_source_changed")
    mapping = _evidence_map(old_repo, new_repo)
    candidate = correction["corrected_candidate_json"]
    if not isinstance(candidate, dict) or set(candidate) != {
            "raw_value", "raw_unit", "validated_value", "validated_unit"}:
        raise CorrectionError("correction_source_changed")
    request = SubmitCorrection(
        case_id=correction["case_id"], source_run_id=source_run["id"],
        fact_set_id=fact_set.fact_set_id, fact_id=old.fact_id,
        raw_value=candidate["raw_value"], raw_unit=candidate["raw_unit"],
        reason=correction["reason"], expected_revision=source_run["revision"],
        idempotency_key="recheck",
    )
    try:
        corrected_old = CorrectionRepository._corrected_fact(
            request, fact_set, old, old_repo)
        expected_refs = [mapping[ref] for ref in old.evidence_refs]
        if (candidate["validated_value"] != str(corrected_old.value)
                or candidate["validated_unit"] != corrected_old.unit
                or correction["target_evidence_ids_json"] != expected_refs):
            raise CorrectionError("correction_source_changed")
        if isinstance(fact_set, FieldFactSet):
            corrected = _validate_candidate(FieldCandidate(
                field_key=fact_set.field_key,
                raw_value=request.raw_value, raw_unit=request.raw_unit,
                evidence_refs=expected_refs,
            ), new_repo, fact_set.supplier)
        else:
            corrected = validate_memory(Candidate(
                raw_value=request.raw_value, raw_unit=request.raw_unit,
                qualifier=old.qualifier, comparator=old.comparator,
                evidence_refs=expected_refs,
            ), new_repo, fact_set.supplier)
        stable = ("value", "unit", "qualifier", "comparator", "role", "supplier", "model")
        if isinstance(fact_set, FieldFactSet):
            stable += ("field_key", "attributes")
        if any(getattr(corrected, key) != getattr(corrected_old, key) for key in stable):
            raise CorrectionError("correction_source_changed")
        old_facts = [_rebind(fact, tuple(mapping[ref] for ref in fact.evidence_refs),
                             new_repo, fact_set.supplier) for fact in fact_set.facts]
        scanned = tuple(mapping[ref] for ref in fact_set.scanned_ids)
    except (KeyError, TypeError, ValueError) as exc:
        raise CorrectionError("correction_source_changed") from exc
    # Both old and corrected values are supported by the same immutable source.
    # Retain both, so a human correction cannot erase a conflicting source fact.
    facts = tuple({fact.fact_id: fact for fact in [*old_facts, corrected]}.values())
    if isinstance(fact_set, FieldFactSet):
        identity = digest([
            new_repo.snapshot_id, fact_set.supplier, fact_set.field_key.value,
            scanned, fact_set.complete,
            [asdict(fact) | {"value": str(fact.value)} for fact in facts],
        ])
        rebound = FieldFactSet(
            fact_set_id=identity, snapshot_id=new_repo.snapshot_id,
            supplier=fact_set.supplier, field_key=fact_set.field_key,
            facts=facts, scanned_ids=scanned, complete=fact_set.complete,
            coverage_issues=fact_set.coverage_issues,
        )
    else:
        identity = digest([
            new_repo.snapshot_id, fact_set.supplier, scanned, fact_set.complete,
            [asdict(fact) | {"value": str(fact.value)} for fact in facts],
        ])
        rebound = FactSet(identity, fact_set.supplier, facts, scanned,
                          fact_set.complete)
    encoded = encode_fact_sets(new_repo, {identity: rebound})
    if encoded is None:
        raise CorrectionError("correction_recheck_capacity")
    try:
        decode_fact_sets(new_repo, encoded)
    except ValueError as exc:
        raise CorrectionError("correction_source_changed") from exc
    if isinstance(rebound, FieldFactSet):
        result = evaluate_field(new_repo, rebound.supplier, rebound.field_key,
                                rebound.facts, scan_complete=rebound.complete)
    else:
        result = evaluate_memory(new_repo, rebound.supplier,
                                 list(rebound.facts), scan_complete=rebound.complete)
    review_status = "needs_review"  # distinct old and new source-backed values
    return {"review_status": review_status,
            "repository_fingerprint": _review_fingerprint(new_repo),
            "corrected_fact_id": corrected.fact_id,
            "fact_set_json": encoded[0],
            "check_result_json": _plain(asdict(result))}


class CorrectionRecheck:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.corrections = CorrectionRepository(engine)

    def recheck(self, case_id: str, correction_id: str) -> dict:
        with self.engine.connect() as connection:
            row = connection.execute(select(corrections).where(
                corrections.c.id == correction_id,
                corrections.c.case_id == case_id,
            )).mappings().first()
            if row is None:
                raise CorrectionError("not_found")
            current = connection.scalar(select(cases.c.revision).where(cases.c.id == case_id))
            target_revision = connection.scalar(select(input_snapshots.c.revision).where(
                input_snapshots.c.id == row["to_snapshot_id"],
                input_snapshots.c.case_id == case_id,
            ))
            existing = connection.scalar(select(correction_reviews.c.correction_id).where(
                correction_reviews.c.correction_id == correction_id,
            ))
            if existing is None and current != target_revision:
                raise CorrectionError("revision_conflict")
            run = (connection.execute(select(agent_runs).where(
                agent_runs.c.id == row["source_run_id"],
            )).mappings().first() if existing is None else None)
        if existing is not None:
            return self.corrections.get_correction(case_id, correction_id)
        if run is None:
            raise CorrectionError("correction_source_changed")
        old_repo, sets = self.corrections._facts(run)
        if old_repo is None:
            raise CorrectionError("correction_source_changed")
        try:
            new_repo = load_agent_snapshot(self.engine, case_id, row["to_snapshot_id"])
            prepared = _prepared(row, run, old_repo, sets, new_repo)
        except SnapshotLoadError as exc:
            raise CorrectionError(exc.code) from exc
        except (TypeError, ValueError) as exc:
            raise CorrectionError("correction_source_changed") from exc
        now = datetime.now(UTC).replace(tzinfo=None)
        try:
            with self.engine.begin() as connection:
                locked_revision = connection.scalar(select(cases.c.revision).where(
                    cases.c.id == case_id).with_for_update())
                if locked_revision != target_revision:
                    raise CorrectionError("revision_conflict")
                existing = connection.scalar(select(correction_reviews.c.correction_id).where(
                    correction_reviews.c.correction_id == correction_id,
                ))
                if existing is None:
                    connection.execute(insert(correction_reviews).values(
                        correction_id=correction_id, case_id=case_id,
                        snapshot_id=row["to_snapshot_id"], created_at=now,
                        **prepared,
                    ))
        except IntegrityError:
            with self.engine.connect() as connection:
                if connection.scalar(select(correction_reviews.c.correction_id).where(
                        correction_reviews.c.correction_id == correction_id)) is None:
                    raise
        return self.corrections.get_correction(case_id, correction_id)
