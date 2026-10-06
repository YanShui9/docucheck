"""Bounded domain memory for a new Agent process on the same input snapshot."""

from dataclasses import asdict

from langchain_core.messages import AIMessage, HumanMessage

from ..evidence.repository import digest
from ..extraction.memory import FactSet
from ..field_contract import FieldCheckResult, FieldFactSet, FieldKey
from ..rules.fields import RULE_VERSION as FIELDS_RULE_VERSION
from ..rules.fields import (
    _evaluate_field_v1,
    _evaluate_field_v2,
    _evaluate_field_v3,
    evaluate_field,
)
from ..rules.memory import RULE_VERSION as MEMORY_RULE_VERSION
from ..rules.memory import CheckResult, evaluate_memory
from .fact_memory import decode_fact_sets, encode_fact_sets

MAX_RESULTS = 24  # three suppliers x eight fields
MAX_REFS_PER_RESULT = 256
MAX_FACT_IDS = 256
BUSINESS_STATUSES = {"满足", "不满足", "资料冲突", "证据不足", "待人工确认"}
LEGACY_FIELD_EVALUATORS = {
    "fields-v1": _evaluate_field_v1,
    "fields-v2": _evaluate_field_v2,
    "fields-v3": _evaluate_field_v3,
}


def _recalculate_result(repo, result, fact_set, *, allow_legacy=False):
    if fact_set is None:
        raise ValueError("memory_scope")
    if isinstance(fact_set, FactSet):
        return evaluate_memory(repo, result.supplier, list(fact_set.facts),
                               scan_complete=fact_set.complete)
    evaluator = (LEGACY_FIELD_EVALUATORS.get(result.rule_version, evaluate_field)
                 if allow_legacy else evaluate_field)
    return evaluator(repo, result.supplier, fact_set.field_key,
                     fact_set.facts, scan_complete=fact_set.complete,
                     failed=result.processing_status == "failed")


def verify_check_result(repo, result, fact_set, *, allow_legacy=False) -> None:
    """Require exact replay; historical reads may use the saved known rule version."""
    verified = _recalculate_result(repo, result, fact_set, allow_legacy=allow_legacy)
    if asdict(verified) != asdict(result):
        raise ValueError("memory_scope")


def _fact_sets_by_pair(fact_sets):
    return {(item.supplier, item.field_key if isinstance(item, FieldFactSet)
             else FieldKey.MEMORY): item for item in fact_sets.values()}


def _repository_fingerprint(repo) -> str:
    documents = sorted((document.document_id, document.source_hash,
                        document.parsed.parser_version,
                        document.parsed.status,
                        tuple(document.parsed.coverage_issues))
                       for document in repo.documents)
    return digest([documents, sorted(block.evidence_id for block in repo.blocks)])


def _binding_fingerprint(repo) -> str:
    return digest(sorted((d.document_id, d.role, d.supplier or "", d.model or "") for d in repo.documents))


def serialize_session(session, last_answer: str) -> dict:
    """Store bounded verified facts and results, never raw model tool messages."""
    encoded_facts = encode_fact_sets(session.repo, session.tools.fact_sets)
    latest = {}
    for result in reversed(list(session.tools.results.values())):
        latest.setdefault((result.supplier, str(result.field_key)), result)
    results = list(reversed(list(latest.values())))[:MAX_RESULTS]
    if encoded_facts is not None:
        # A new extraction can replace a fact set without evaluating it yet.
        # Carry only results that correspond to the latest serialized facts.
        by_pair = _fact_sets_by_pair(session.tools.fact_sets)
        aligned = []
        for result in results:
            try:
                verify_check_result(session.repo, result, by_pair.get(
                    (result.supplier, FieldKey(result.field_key))))
            except ValueError:
                continue
            aligned.append(result)
        results = aligned
    return {
        "version": 3,
        "snapshot_id": session.repo.snapshot_id,
        "repository_fingerprint": _repository_fingerprint(session.repo),
        "binding_fingerprint": _binding_fingerprint(session.repo),
        "fact_sets_available": encoded_facts is not None,
        "fact_sets": encoded_facts if encoded_facts is not None else [],
        "focus": session.focus,
        "pending_clarification": session.pending_clarification,
        "check_results": [{**asdict(result),
                           "evidence_refs": list(result.evidence_refs),
                           "reason_evidence_refs": list(result.reason_evidence_refs),
                           "fact_ids": list(result.fact_ids)} for result in results
                          if len(result.evidence_refs) <= MAX_REFS_PER_RESULT
                          and len(result.fact_ids) <= MAX_FACT_IDS],
        "recent_turns": [{"question": str(question.content)[:2000],
                          "answer": str(answer.content)[:20_000]}
                         for question, answer in session.history[-8:]],
        "last_answer": last_answer[:20_000],
    }


def _valid_strings(value, *, limit: int) -> bool:
    return (isinstance(value, list) and len(value) <= limit
            and all(isinstance(item, str) and 0 < len(item) <= 128
                    for item in value))


def _result(session, value: dict, *, allow_legacy=False):
    try:
        field = FieldKey(value["field_key"])
        # A memory check can come from the dedicated single-field rule or the
        # generic eight-field extractor. Its rule version identifies the type.
        allowed_versions = ({MEMORY_RULE_VERSION, FIELDS_RULE_VERSION}
                            if field == FieldKey.MEMORY else {FIELDS_RULE_VERSION})
        if allow_legacy:
            allowed_versions.update(LEGACY_FIELD_EVALUATORS)
        if (value["snapshot_id"] != session.repo.snapshot_id
                or value["rule_version"] not in allowed_versions
                or value["processing_status"] not in {"complete", "partial", "failed"}
                or value["business_status"] not in BUSINESS_STATUSES | {None}
                or not isinstance(value["check_result_id"], str)
                or not value["check_result_id"]
                or not isinstance(value["reason_code"], str)
                or not value["reason_code"]
                or not _valid_strings(value["evidence_refs"], limit=MAX_REFS_PER_RESULT)
                or not _valid_strings(value["reason_evidence_refs"],
                                      limit=MAX_REFS_PER_RESULT)
                or not _valid_strings(value["fact_ids"], limit=MAX_FACT_IDS)
                or not set(value["reason_evidence_refs"]).issubset(
                    value["evidence_refs"])):
            raise ValueError("memory_scope")
        session.repo.assert_supplier(value["supplier"])
        blocks = session.repo.read(value["evidence_refs"])
        if any(block.supplier not in {None, value["supplier"]}
               for block in blocks):
            raise ValueError("memory_scope")
        data = {**value, "field_key": field,
                "evidence_refs": tuple(value["evidence_refs"]),
                "reason_evidence_refs": tuple(value["reason_evidence_refs"]),
                "fact_ids": tuple(value["fact_ids"])}
        if field == FieldKey.MEMORY and value["rule_version"] == MEMORY_RULE_VERSION:
            return CheckResult(**data)
        return FieldCheckResult(**data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("memory_scope") from exc


def restore_session(session, memory: dict, history: list[tuple]) -> None:
    """Validate every carried handle before exposing it to a new model call."""
    if (not isinstance(memory, dict) or memory.get("version") not in {1, 2, 3}
            or memory.get("snapshot_id") != session.repo.snapshot_id
            or memory.get("repository_fingerprint") !=
            _repository_fingerprint(session.repo)
            or not isinstance(memory.get("check_results"), list)
            or len(memory["check_results"]) > MAX_RESULTS):
        raise ValueError("memory_scope")
    if memory["version"] == 3 and memory.get("binding_fingerprint") != _binding_fingerprint(session.repo):
        raise ValueError("memory_scope")
    focus = memory.get("focus")
    if focus is not None:
        try:
            if set(focus) != {"supplier", "field_key"}:
                raise ValueError("memory_scope")
            session.repo.assert_supplier(focus["supplier"])
            FieldKey(focus["field_key"])
        except (TypeError, ValueError) as exc:
            raise ValueError("memory_scope") from exc
    pending = memory.get("pending_clarification")
    if pending is not None and (not isinstance(pending, str)
                                or not 0 < len(pending) <= 2000):
        raise ValueError("memory_scope")
    results = [_result(session, value, allow_legacy=True) for value in memory["check_results"]]
    if len({result.check_result_id for result in results}) != len(results):
        raise ValueError("memory_scope")
    fact_sets = {}
    available = False
    if memory["version"] in {2, 3}:
        available = memory.get("fact_sets_available")
        payload = memory.get("fact_sets")
        if type(available) is not bool or not isinstance(payload, list):
            raise ValueError("memory_scope")
        if available:
            fact_sets = decode_fact_sets(session.repo, payload)
        elif payload:
            raise ValueError("memory_scope")
    if available:
        by_pair = _fact_sets_by_pair(fact_sets)
        current_results = []
        for result in results:
            fact_set = by_pair.get((result.supplier, FieldKey(result.field_key)))
            verify_check_result(session.repo, result, fact_set, allow_legacy=True)
            # Verify the original ID/outcome first, then recompute a new handle.
            # The saved memory and the archived Run are never relabeled.
            current_results.append(_recalculate_result(session.repo, result, fact_set)
                                   if result.rule_version in LEGACY_FIELD_EVALUATORS else result)
        results = current_results
    else:
        # Legacy or oversized memory keeps conversational context, but cannot
        # provide verified handles to the next model without source facts.
        results = []
    turns = memory.get("recent_turns")
    if turns is not None:
        if (not isinstance(turns, list) or len(turns) > (8 if memory["version"] == 3 else 4)
                or any(not isinstance(turn, dict)
                       or set(turn) != {"question", "answer"}
                       or not isinstance(turn["question"], str)
                       or not isinstance(turn["answer"], str)
                       or not 0 < len(turn["question"]) <= 2000
                       or not 0 < len(turn["answer"]) <= 20_000
                       for turn in turns)):
            raise ValueError("memory_scope")
        restored_history = [(HumanMessage(content=turn["question"]),
                             AIMessage(content=turn["answer"])) for turn in turns]
    else:
        restored_history = history[-8:] if memory["version"] == 3 else history[-4:]
    session.tools.results = {result.check_result_id: result for result in results}
    session.tools.fact_sets = fact_sets
    session.tools.seen = {ref for result in results for ref in result.evidence_refs}
    session.focus = focus
    session.pending_clarification = pending
    session.history = restored_history
