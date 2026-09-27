"""Conservative, source-bound validation of memory candidates."""
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from openpyxl.utils.cell import column_index_from_string, coordinate_from_string
from pydantic import BaseModel, ConfigDict, Field

from ..evidence.models import ScopedEvidence
from ..evidence.repository import EvidenceRepository, digest
from ..rules.fields import normalize_field_value

MEMORY_LABEL = re.compile(r"内存|\bRAM\b", re.IGNORECASE)
PAIR_LABEL = re.compile(r"\s*(?:已装|安装|标配)?\s*(?:内存|RAM)(?:容量)?\s*", re.IGNORECASE)
CAPACITY = re.compile(r"\d+(?:\.\d+)?\s*(?:MiB|GiB|GB)\b", re.IGNORECASE)
NUMERIC_CELL = re.compile(r"\s*\d+(?:\.\d+)?\s*(?:MiB|GiB|GB)\s*", re.IGNORECASE)
OTHER_CAPACITY = re.compile(r"内存卡|虚拟内存|共享内存|存储|SSD|硬盘|显存|缓存", re.IGNORECASE)
HEADER_HINT = re.compile(r"内存|RAM|容量|最大|最高|支持|标配|选配|可选|已装|存储|SSD|硬盘", re.IGNORECASE)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_value: str = Field(pattern=r"^\d+(\.\d+)?$", max_length=20)
    raw_unit: Literal["GB", "GiB", "MiB"]
    qualifier: Literal["installed", "maximum", "unknown"]
    comparator: Literal["gte", "lte", "gt", "lt", "eq", "unknown"]
    evidence_refs: list[str] = Field(min_length=1, max_length=2)


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


@dataclass(frozen=True)
class MemoryCellContext:
    positions: dict[str, tuple[int, int]]
    locations: dict[tuple[str, str, int, int], ScopedEvidence]
    row_capacities: dict[tuple[str, str, int], set[str]]
    first_column_header: dict[tuple[str, str, int], int]


def _cell_position(block: ScopedEvidence) -> tuple[int, int] | None:
    if block.sheet is None or block.cell is None:
        return None
    try:
        column, row = coordinate_from_string(block.cell)
        return column_index_from_string(column), row
    except ValueError:
        return None


def build_memory_cell_context(blocks: list[ScopedEvidence]) -> MemoryCellContext:
    positions, locations, row_capacities, first_column_header = {}, {}, {}, {}
    for block in blocks:
        position = _cell_position(block)
        if position is None or block.sheet is None:
            continue
        column, row = position
        positions[block.evidence_id] = position
        locations[(block.document_id, block.sheet, column, row)] = block
        if CAPACITY.search(block.text):
            row_capacities.setdefault((block.document_id, block.sheet, row), set()).add(
                block.evidence_id
            )
        elif HEADER_HINT.search(block.text):
            key = (block.document_id, block.sheet, column)
            first_column_header[key] = min(row, first_column_header.get(key, row))
    return MemoryCellContext(positions, locations, row_capacities, first_column_header)


def _memory_pair(label: ScopedEvidence, value: ScopedEvidence,
                 context: MemoryCellContext) -> bool:
    left = context.positions.get(label.evidence_id)
    right = context.positions.get(value.evidence_id)
    repeated_label_value = bool(
        re.fullmatch(
            rf"\s*{re.escape(label.text.strip())}\s*{CAPACITY.pattern}\s*",
            value.text,
            re.IGNORECASE,
        )
    )
    if not (left is not None and right is not None and left[1] == right[1]
            and right[0] == left[0] + 1 and label.document_id == value.document_id
            and label.sheet == value.sheet and label.role == value.role
            and label.supplier == value.supplier and label.model == value.model
            and PAIR_LABEL.fullmatch(label.text) is not None
            and OTHER_CAPACITY.search(label.text) is None
            and CAPACITY.search(label.text) is None
            and (NUMERIC_CELL.fullmatch(value.text) is not None or repeated_label_value)):
        return False
    other_capacities = context.row_capacities.get((label.document_id, label.sheet, right[1]), set())
    header_row = context.first_column_header.get((label.document_id, label.sheet, right[0]))
    return (not (other_capacities - {value.evidence_id})
            and (header_row is None or header_row >= right[1]))


def xlsx_memory_relationships(
    blocks: list[ScopedEvidence], context: MemoryCellContext | None = None
) -> tuple[dict[str, str], set[str]]:
    """Return unambiguous label/value pairs and labels needing more coverage."""
    context = context or build_memory_cell_context(blocks)
    pairs, unresolved = {}, set()
    for label in blocks:
        if label.sheet is None or not MEMORY_LABEL.search(label.text) or CAPACITY.search(label.text):
            continue
        position = context.positions.get(label.evidence_id)
        if position is None:
            unresolved.add(label.evidence_id)
            continue
        right = context.locations.get((label.document_id, label.sheet, position[0] + 1, position[1]))
        if (right is not None and NUMERIC_CELL.fullmatch(right.text) is not None
                and _memory_pair(label, right, context)):
            pairs[label.evidence_id] = right.evidence_id
        elif right is None or not (MEMORY_LABEL.search(right.text) and CAPACITY.search(right.text)):
            unresolved.add(label.evidence_id)
    return pairs, unresolved


def validate_memory(candidate: dict | Candidate, repo: EvidenceRepository, supplier: str,
                    *, cell_context: MemoryCellContext | None = None) -> VerifiedFact:
    item = candidate if isinstance(candidate, Candidate) else Candidate.model_validate(candidate)
    repo.assert_supplier(supplier)
    selected = repo.read(item.evidence_refs)
    if len(selected) == 2:
        cell_context = cell_context or build_memory_cell_context(repo.supplier_blocks(supplier))
        selected.sort(key=lambda block: cell_context.positions.get(block.evidence_id, (0, 0)))
        if not _memory_pair(selected[0], selected[1], cell_context):
            raise ValueError("fact_field")
        item = item.model_copy(update={"evidence_refs": [block.evidence_id for block in selected]})
    block = selected[0]
    if block.role != "requirement" and block.supplier != supplier:
        raise ValueError("supplier_scope")
    text = " ".join(source.text for source in selected)
    clauses = re.split(r"[;；。\n]", text)
    candidates = [clause for clause in clauses if MEMORY_LABEL.search(clause)
                  and not OTHER_CAPACITY.search(clause)
                  and re.search(r"\d\s*(?:MiB|GiB|GB)\b", clause, re.IGNORECASE)]
    if len(candidates) != 1:
        raise ValueError("fact_field")
    text = candidates[0]
    values = re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(MiB|GiB|GB)\b", text, re.IGNORECASE)
    value = Decimal(item.raw_value)
    if value <= 0 or not any(Decimal(v) == value and u.casefold() == item.raw_unit.casefold() for v, u in values):
        raise ValueError("fact_value")
    # Multiple capacities or approximation in one evidence span require review.
    ambiguous = len(values) != 1 or bool(re.search(r"约|可选|可升级|或|approximately", text, re.IGNORECASE))
    observed_qualifier = ("maximum" if re.search(r"最大|最高|max", text, re.IGNORECASE)
                          else "installed" if re.search(r"已装|installed", text, re.IGNORECASE) else "unknown")
    if item.qualifier != "unknown" and item.qualifier != observed_qualifier:
        raise ValueError("fact_qualifier")
    observed_comparator = (
        "gte" if re.search(r"至少|不低于|>=|≥", text) else
        "lte" if re.search(r"不超过|不高于|至多|<=|≤", text) else
        "gt" if re.search(r"(?<![<>])>(?!=)|大于|多于|超过|高于", text) else
        "lt" if re.search(r"(?<![<>])<(?!=)|小于|少于|低于", text) else "eq"
    )
    if item.comparator != "unknown" and item.comparator != observed_comparator:
        raise ValueError("fact_comparator")
    qualifier = "unknown" if ambiguous else item.qualifier
    normalized_value, normalized_unit = normalize_field_value("memory", item.raw_value, item.raw_unit)
    return VerifiedFact(digest([repo.snapshot_id, item.model_dump()]), normalized_value, normalized_unit,
                        qualifier, item.comparator, block.role, block.supplier, block.model,
                        tuple(item.evidence_refs))
