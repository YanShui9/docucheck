"""Finite buyer edits preserve all untouched identities and original provenance."""

from copy import deepcopy

import pytest

from backend.app.criteria.models import ChecklistDefinition
from backend.tests.test_checklists import definition as raw_definition


def definition(count=10):
    return ChecklistDefinition.model_validate(raw_definition(count)).model_dump(mode="json")


def apply(base, patch, instruction="把数量改成20，并添加包装要求"):
    from backend.app.criteria.changes import apply_changes

    return apply_changes(ChecklistDefinition.model_validate(base), patch, instruction)


def test_patch_preserves_untouched_ids_and_program_generates_diff():
    base = definition()
    original = deepcopy(base)
    target = deepcopy({k: v for k, v in base["criteria"][0].items() if k not in {"source", "criterion_id"}})
    target["rule"]["value"] = "20"
    patch = {"items": [], "operations": [{"op": "update", "criterion_id": base["criteria"][0]["criterion_id"], "criterion": target, "reason": "调整采购数量"}]}
    candidate, changes = apply(base, patch)
    assert base == original
    assert candidate.items == ChecklistDefinition.model_validate(base).items
    assert candidate.criteria[0].criterion_id == base["criteria"][0]["criterion_id"]
    assert candidate.criteria[0].source.kind == "user_defined"
    assert "把数量改成20" in candidate.criteria[0].source.reason
    assert changes[0]["op"] == "update" and changes[0]["before"] == base["criteria"][0]
    assert changes[0]["after"] == candidate.criteria[0].model_dump(mode="json")


def test_add_does_not_replace_existing_criteria_and_remove_is_explicit():
    base = definition()
    target = {"item_id": base["items"][0]["item_id"], "name": "包装", "rule": {"type": "presence", "description": "完整包装"}}
    patch = {"items": [], "operations": [{"op": "add", "criterion": target, "reason": "用户补充包装"}]}
    candidate, changes = apply(base, patch)
    assert candidate.criteria[0].model_dump(mode="json") == base["criteria"][0]
    assert len(candidate.criteria) == len(base["criteria"]) + 1
    assert changes[0]["op"] == "add"
    assert apply(base, patch)[0] == candidate
    removed, _ = apply(candidate.model_dump(mode="json"), {"items": [], "operations": [{"op": "remove", "criterion_id": candidate.criteria[-1].criterion_id, "reason": "取消新增项"}]})
    assert removed.model_dump(mode="json") == base


@pytest.mark.parametrize("operations", [
    [{"op": "remove", "criterion_id": "foreign", "reason": "删除"}],
    [{"op": "remove", "criterion_id": "rice-0", "reason": "删除"}],
])
def test_unknown_target_or_empty_checklist_is_rejected(operations):
    with pytest.raises(ValueError):
        apply(definition(1), {"items": [], "operations": operations})


def test_update_cannot_move_existing_metric_to_another_item_or_erase_omitted_flags():
    base = definition(1)
    target = {k: v for k, v in base["criteria"][0].items() if k not in {"source", "criterion_id"}}
    target["item_id"] = "another"
    patch = {"items": [{"item_id": "another", "name": "另一品目"}], "operations": [
        {"op": "update", "criterion_id": "rice-0", "criterion": target, "reason": "调整"}]}
    with pytest.raises(ValueError, match="criterion_item_changed"):
        apply(base, patch)
    target["item_id"] = "rice"
    target.pop("conditions")
    with pytest.raises(ValueError):
        apply(base, patch)
