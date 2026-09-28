"""Conservative admission check for a complete review's model-call budget."""

from ..extraction.fields import _batches
from ..field_contract import FieldKey
from .answer import QuestionScope


def review_capacity(repo, scope: QuestionScope, *, max_calls: int,
                    max_decisions: int) -> tuple[int, int] | None:
    """Return required field batches and minimum calls when the review cannot fit."""
    if not scope.full_review or scope.required_pairs is None:
        return None
    suppliers = sorted({supplier for supplier, _ in scope.required_pairs})
    fields = {field for _, field in scope.required_pairs}
    field_batches = 0
    oversized = False
    if fields - {FieldKey.MEMORY.value}:
        for supplier in suppliers:
            batches, issues = _batches(repo.supplier_blocks(supplier))
            field_batches += len(batches)
            oversized |= bool(issues)
    memory_calls = 0
    if FieldKey.MEMORY.value in fields:
        for supplier in suppliers:
            blocks = repo.supplier_blocks(supplier)
            memory_calls += len({block.document_id for block in blocks})
    decisions = 2 * len(suppliers) + 1
    minimum_calls = field_batches + memory_calls + decisions
    if oversized or minimum_calls > max_calls or decisions > max_decisions:
        return field_batches, minimum_calls
    return None
