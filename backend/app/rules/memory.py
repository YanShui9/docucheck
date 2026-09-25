"""Memory checks never promote incomplete processing into a business conclusion."""
from dataclasses import dataclass

from ..evidence.repository import EvidenceRepository, digest
from ..extraction.validation import VerifiedFact

RULE_VERSION = "memory-v1"


@dataclass(frozen=True)
class CheckResult:
    check_result_id: str
    snapshot_id: str
    supplier: str
    field_key: str
    processing_status: str
    business_status: str | None
    reason_code: str
    evidence_refs: tuple[str, ...]
    fact_ids: tuple[str, ...]
    rule_version: str = RULE_VERSION


def evaluate_memory(repo: EvidenceRepository, supplier: str, facts: list[VerifiedFact],
                    *, scan_complete: bool) -> CheckResult:
    coverage = repo.coverage(supplier)
    refs = tuple(dict.fromkeys(ref for f in facts for ref in f.evidence_refs))
    if scan_complete and coverage["complete"]:
        repo.read(list(refs))
    requirements = [f for f in facts if f.role == "requirement"]
    offered = [f for f in facts if f.supplier == supplier]
    status, reason, processing = None, "coverage_incomplete", "partial"
    if scan_complete and coverage["complete"]:
        processing = "complete"
        if not requirements:
            status, reason = "证据不足", "requirement_missing"
        elif (any(f.qualifier != "installed" or f.comparator == "unknown" for f in facts)
              or len({(f.value, f.unit, f.comparator) for f in requirements}) != 1
              or len({f.unit for f in facts}) != 1
              or any(not f.model for f in offered)
              or len({f.model for f in offered}) > 1):
            status, reason = "待人工确认", "semantics_unclear"
        elif not offered:
            status, reason = "证据不足", "supplier_fact_missing"
        elif any(f.comparator != "eq" for f in offered):
            status, reason = "待人工确认", "supplier_capacity_not_exact"
        elif len({f.value for f in offered}) > 1:
            status, reason = "资料冲突", "contradicting_installed_capacity"
        else:
            requirement, actual = requirements[0], offered[0].value
            passes = {"gte": actual >= requirement.value, "lte": actual <= requirement.value,
                      "eq": actual == requirement.value}[requirement.comparator]
            status, reason = ("满足", "threshold_met") if passes else ("不满足", "threshold_failed")
    identity = digest([repo.snapshot_id, supplier, RULE_VERSION, [f.fact_id for f in facts], status, reason])
    return CheckResult(identity, repo.snapshot_id, supplier, "memory", processing, status, reason,
                       refs, tuple(f.fact_id for f in facts))
