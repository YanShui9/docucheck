"""Conservative, source-bound validation of memory candidates."""
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..evidence.repository import EvidenceRepository, digest


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_value: str = Field(pattern=r"^\d+(\.\d+)?$", max_length=20)
    raw_unit: Literal["GB", "GiB"]
    qualifier: Literal["installed", "maximum", "unknown"]
    comparator: Literal["gte", "lte", "eq", "unknown"]
    evidence_refs: list[str] = Field(min_length=1, max_length=1)


class MemoryCandidates(BaseModel):
    """All memory capacity facts found in this batch, including contradictions."""
    model_config = ConfigDict(extra="forbid")
    facts: list[Candidate] = Field(max_length=64)


@dataclass(frozen=True)
class VerifiedFact:
    fact_id: str
    value: Decimal
    unit: str
    qualifier: str
    comparator: str
    role: str
    supplier: str | None
    model: str | None
    evidence_refs: tuple[str, ...]


def validate_memory(candidate: dict | Candidate, repo: EvidenceRepository, supplier: str) -> VerifiedFact:
    item = candidate if isinstance(candidate, Candidate) else Candidate.model_validate(candidate)
    repo.assert_supplier(supplier)
    block = repo.read(item.evidence_refs)[0]
    if block.role != "requirement" and block.supplier != supplier:
        raise ValueError("supplier_scope")
    clauses = re.split(r"[;；。\n]", block.text)
    candidates = [clause for clause in clauses if re.search(r"内存|\bRAM\b", clause, re.IGNORECASE)
                  and not re.search(r"存储|SSD|硬盘|显存|缓存", clause, re.IGNORECASE)
                  and re.search(r"\d\s*(?:GiB|GB)\b", clause, re.IGNORECASE)]
    if len(candidates) != 1:
        raise ValueError("fact_field")
    text = candidates[0]
    values = re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(GiB|GB)\b", text, re.IGNORECASE)
    value = Decimal(item.raw_value)
    if value <= 0 or not any(Decimal(v) == value and u.casefold() == item.raw_unit.casefold() for v, u in values):
        raise ValueError("fact_value")
    # Multiple capacities or approximation in one evidence span require review.
    ambiguous = len(values) != 1 or bool(re.search(r"约|可选|可升级|或|approximately", text, re.IGNORECASE))
    observed_qualifier = ("maximum" if re.search(r"最大|最高|max", text, re.IGNORECASE)
                          else "installed" if re.search(r"已装|installed", text, re.IGNORECASE) else "unknown")
    if item.qualifier != "unknown" and item.qualifier != observed_qualifier:
        raise ValueError("fact_qualifier")
    observed_comparator = ("gte" if re.search(r"至少|不低于|>=|≥", text) else
                           "lte" if re.search(r"不超过|至多|<=|≤", text) else "eq")
    if re.search(r"(?<![<>])[<>](?!=)|大于|小于|多于|少于|超过|低于", text) and not re.search(r"不超过|不低于", text):
        observed_comparator = "unknown"
    if item.comparator != "unknown" and item.comparator != observed_comparator:
        raise ValueError("fact_comparator")
    qualifier = "unknown" if ambiguous else item.qualifier
    return VerifiedFact(digest([repo.snapshot_id, item.model_dump()]), value, item.raw_unit,
                        qualifier, item.comparator, block.role, block.supplier, block.model,
                        tuple(item.evidence_refs))
