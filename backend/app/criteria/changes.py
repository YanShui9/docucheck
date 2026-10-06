"""Apply finite user edits to a fixed base, without implicit removal or confirmation."""

from typing import Annotated, Literal

from pydantic import Field

from ..evidence.repository import digest
from .models import (
    ChecklistDefinition,
    Contract,
    Criterion,
    Identifier,
    Label,
    ProcurementItem,
    Rule,
    UserDefinedSource,
)


class CriterionEdit(Contract):
    item_id: Identifier
    name: Label
    rule: Rule
    required: bool = True
    enabled: bool = True
    conditions: list[Label] = Field(default_factory=list, max_length=8)


class AddCriterion(Contract):
    op: Literal["add"]
    criterion: CriterionEdit
    reason: str = Field(min_length=1, max_length=300, pattern=r"\S")


class ExistingCriterionEdit(CriterionEdit):
    required: bool
    enabled: bool
    conditions: list[Label] = Field(max_length=8)


# An update is a full target value. Omitted flags must not silently reset
# required/enabled or erase conditional requirements.
class UpdateCriterion(Contract):
    op: Literal["update"]
    criterion_id: Identifier
    criterion: ExistingCriterionEdit
    reason: str = Field(min_length=1, max_length=300, pattern=r"\S")


class RemoveCriterion(Contract):
    op: Literal["remove"]
    criterion_id: Identifier
    reason: str = Field(min_length=1, max_length=300, pattern=r"\S")


class ChecklistPatch(Contract):
    items: list[ProcurementItem] = Field(default_factory=list, max_length=20)
    operations: list[Annotated[AddCriterion | UpdateCriterion | RemoveCriterion, Field(discriminator="op")]] = Field(min_length=1, max_length=100)


def apply_changes(base: ChecklistDefinition | None, patch: ChecklistPatch | dict,
                  instruction: str) -> tuple[ChecklistDefinition, list[dict]]:
    """Only explicit targets change. Provenance is owned by the server."""
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 2000:
        raise ValueError("invalid_instruction")
    patch = patch if isinstance(patch, ChecklistPatch) else ChecklistPatch.model_validate(patch)
    items = list(base.items) if base else []
    criteria = {c.criterion_id: c for c in base.criteria} if base else {}
    original_ids = set(criteria)
    item_ids, touched, changes = {i.item_id for i in items}, set(), []
    for item in patch.items:
        if item.item_id in item_ids:
            raise ValueError("item_identity_conflict")
        item_ids.add(item.item_id)
        items.append(item)
    for index, operation in enumerate(patch.operations):
        identity = ("criterion-" + digest([base.model_dump(mode="json") if base else None,
                                           instruction, index, operation.model_dump(mode="json")])[:24]
                    if operation.op == "add" else operation.criterion_id)
        if identity in touched or (operation.op != "add" and identity not in original_ids):
            raise ValueError("criterion_target")
        if operation.op == "add" and identity in criteria:
            raise ValueError("criterion_identity_conflict")
        touched.add(identity)
        before = criteria.get(identity)
        if operation.op == "update" and operation.criterion.item_id != before.item_id:
            raise ValueError("criterion_item_changed")
        after = None
        if operation.op == "remove":
            del criteria[identity]
        else:
            # The immutable payload keeps the full instruction. The source's
            # bounded reason states explicitly when only its prefix fits.
            reason = "用户指令：" + (instruction if len(instruction) <= 640 else instruction[:640] + "（指令节选，完整内容见候选运行）") + "\n变更理由：" + operation.reason
            source = UserDefinedSource(kind="user_defined", reason=reason,
                                       context=before.source.context if before else None)
            after = Criterion(criterion_id=identity, source=source,
                              **operation.criterion.model_dump(mode="json"))
            criteria[identity] = after
        changes.append({"op": operation.op, "criterion_id": identity,
                        "reason": operation.reason,
                        "before": before.model_dump(mode="json") if before else None,
                        "after": after.model_dump(mode="json") if after else None})
    candidate = ChecklistDefinition(items=items, criteria=list(criteria.values()))
    # Every supplied new item must actually be used; no unrelated item mutation.
    if any(i.item_id not in {c.item_id for c in candidate.criteria} for i in patch.items):
        raise ValueError("unused_new_item")
    return candidate, changes
