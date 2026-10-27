"""Only explicit, frozen other-SKU PDF pages may complete bound inventory."""

from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from backend.app.agent.dynamic import review_plan
from backend.app.criteria.item_bindings import validate_bindings, validated_from_frozen
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult


def sample(*, suppliers=("A",), tail=None, partial=False, specification="型号 DEMO-P512"):
    definition = ChecklistDefinition.model_validate({
        "items": [{"item_id": "phone", "name": "苹果手机", "specification": specification}],
        "criteria": [{"criterion_id": "capacity", "item_id": "phone", "name": "存储容量",
                      "rule": {"type": "numeric", "operator": "gte", "value": "512", "unit": "GB"},
                      "source": {"kind": "user_defined", "reason": "明确采购"}}],
    })
    documents = []
    for supplier in suppliers:
        doc_id = f"spec-{supplier}"
        text = [(0, "品目：苹果手机；型号：DEMO-P512"), (0, "存储容量 512 GB"),
                (1, "品目：其他手机；型号：DEMO-OTHER"), (1, "此页产品不在当前采购范围。"),
                (1, "存储容量 256 GB"), (1, "含税单价 6800 CNY/台")]
        if tail is not None:
            text = text[:2] + [(1, line) for line in tail]
        blocks = tuple(EvidenceBlock(str(i), doc_id, line, page_idx=page)
                       for i, (page, line) in enumerate(text))
        parsed = ParseResult(doc_id, "parser", "partial" if partial else "complete", blocks,
                             total_pages=2, processed_pages=(0, 1),
                             coverage_issues=("unreadable",) if partial else ())
        documents.append(Document(doc_id, "a" * 64, "spec", supplier, "DEMO-P512", parsed))
    repo = EvidenceRepository("case", 1, documents)
    entries, exclusions = [], []
    for supplier in suppliers:
        rows = [b for b in repo.blocks if b.supplier == supplier]
        entries.append({"document_id": rows[0].document_id, "source_sha256": "a" * 64,
                        "parser_version": "parser", "item_id": "phone",
                        "selector": {"kind": "pdf", "start_page": 1, "end_page": 1},
                        "anchor": {"evidence_refs": [rows[0].evidence_id], "quote": rows[0].text},
                        "identifier_class": "sku", "supplier_sku": "DEMO-P512",
                        "sku_anchor": {"evidence_refs": [rows[0].evidence_id], "quote": rows[0].text}})
        exclusions.append({"document_id": rows[0].document_id, "source_sha256": "a" * 64,
                           "parser_version": "parser", "page": 2, "other_sku": "DEMO-OTHER",
                           "sku_anchor": {"evidence_refs": [rows[2].evidence_id], "quote": rows[2].text},
                           "reason": "核对该页为另一型号，不含本次共同条款"})
    return definition, repo, entries, exclusions


def freeze(definition, repo, entries, exclusions=()):
    payload = {"schema_version": 2 if exclusions else 1, "case_id": repo.case_id,
               "snapshot_id": repo.snapshot_id, "checklist_version_id": "confirmed",
               "checklist_digest": digest(definition.model_dump(mode="json")),
               "entries": validate_bindings(entries, definition, repo).payload()}
    if exclusions:
        payload["exclusions"] = []
        for raw in exclusions:
            blocks = [b for b in repo.blocks if b.document_id == raw["document_id"]
                      and b.page_idx == raw["page"] - 1]
            anchors = repo.read(raw["sku_anchor"]["evidence_refs"])
            payload["exclusions"].append(raw | {
                "supplier": blocks[0].supplier, "evidence_refs": sorted(b.evidence_id for b in blocks),
                "source_fingerprint": digest([asdict(b) for b in sorted(blocks, key=lambda b: b.evidence_id)]),
                "anchor_fingerprint": digest([asdict(b) for b in anchors]),
            })
        payload["exclusions"].sort(key=lambda e: (e["document_id"], e["page"]))
    return {"version_id": "binding", "digest": digest(payload), "payload": payload}


def test_v16_explicit_other_sku_page_completes_inventory_without_extracting_it():
    definition, repo, entries, exclusions = sample()
    frozen = freeze(definition, repo, entries, exclusions)
    plans, complete = review_plan(repo, definition, rule_version="dynamic-rules-v16", item_bindings=frozen)
    assert complete == {"A": {"phone": True}}
    planned_refs = {b["evidence_id"] for batch, _, _ in plans["A"] for b in batch}
    assert planned_refs == {b.evidence_id for b in repo.blocks if b.page_idx == 0}


def test_unconfirmed_pages_and_other_supplier_inventory_still_block():
    definition, repo, entries, exclusions = sample(suppliers=("A", "B"))
    frozen = freeze(definition, repo, entries, exclusions[:1])
    _, complete = review_plan(repo, definition, rule_version="dynamic-rules-v16", item_bindings=frozen)
    assert complete == {"A": {"phone": True}, "B": {"phone": False}}
    _, complete = review_plan(repo, definition, rule_version="dynamic-rules-v16",
                              item_bindings=freeze(definition, repo, entries))
    assert complete == {"A": {"phone": False}, "B": {"phone": False}}


@pytest.mark.parametrize("rule_version", ["dynamic-rules-v4", "dynamic-rules-v14", "dynamic-rules-v15"])
def test_legacy_rules_reject_new_scope_and_keep_original_unassigned_guard(rule_version):
    definition, repo, entries, exclusions = sample()
    with pytest.raises(ValueError):
        review_plan(repo, definition, rule_version=rule_version,
                    item_bindings=freeze(definition, repo, entries, exclusions))
    _, complete = review_plan(repo, definition, rule_version=rule_version,
                              item_bindings=freeze(definition, repo, entries))
    assert complete == {"A": {"phone": False}}


@pytest.mark.parametrize("tail", [
    ["品目：其他手机；型号：DEMO-OTHER", "DEMO-P512 存储容量 128 GB"],
    ["品目：其他手机；型号：DEMO-OTHER", "型号：SECOND-SKU"],
    ["品目：其他手机；型号：DEMO-OTHER", "以下单价适用于所有型号"],
    ["品目：其他手机；型号：DEMO-OTHER", "全部产品共同条款"],
    ["品目：其他手机；型号：DEMO-OTHER", "该页付款方式对所有设备通用"],
    ["品目：其他手机；型号：DEMO-OTHER", "These payment terms apply to all products."],
    ["品目：其他手机；型号：DEMO-OTHER", "不是 demo-other"],
    ["品目：其他手机；型号：DEMO-OTHER", "并非 demo-other 产品"],
    ["品目：其他手机；型号：DEMO-OTHER", "不属于 demo-other"],
    ["品目：其他手机；型号：DEMO-OTHER", "苹果手机含税单价 9000 CNY/台"],
    ["品目：其他手机；型号：DEMO-OTHER", "型号：中文复杂型号"],
    ["品目：其他手机；型号：DEMO-OTHER；数量20台"],
    ["不是 DEMO-OTHER 型号产品"],
    ["型号：DEMO-OTHER-LONGER"],
])
def test_mixed_unknown_shared_or_business_heading_cannot_be_excluded(tail):
    from backend.app.criteria.page_exclusions import validate_exclusions

    definition, repo, entries, exclusions = sample(tail=tail)
    with pytest.raises(ValueError):
        validate_exclusions(exclusions, definition, repo, validate_bindings(entries, definition, repo))


@pytest.mark.parametrize("declaration", ["型号：待确认", "型号：未确定", "型号：不限", "型号：任意",
                                          "款号：尚待指定", "SKU：未明确", "型号："])
def test_unknown_sku_declaration_cannot_be_hidden_by_a_valid_other_sku_heading(declaration):
    definition, repo, entries, exclusions = sample(tail=["品目：其他手机；型号：DEMO-OTHER", declaration])
    with pytest.raises(ValueError, match="item_exclusion_mixed_scope"):
        review_plan(repo, definition, rule_version="dynamic-rules-v16",
                    item_bindings=freeze(definition, repo, entries, exclusions))


@pytest.mark.parametrize("change", ["target", "page", "hash", "parser", "anchor", "duplicate", "overlap"])
def test_invalid_exclusion_scope_cannot_be_confirmed(change):
    from backend.app.criteria.page_exclusions import validate_exclusions

    definition, repo, entries, exclusions = sample()
    if change == "target":
        exclusions[0]["other_sku"] = "DEMO-P512"
    elif change == "page":
        exclusions[0]["page"] = 3
    elif change == "hash":
        exclusions[0]["source_sha256"] = "b" * 64
    elif change == "parser":
        exclusions[0]["parser_version"] = "old"
    elif change == "anchor":
        exclusions[0]["sku_anchor"] = entries[0]["anchor"]
    elif change == "duplicate":
        exclusions.append(deepcopy(exclusions[0]))
    elif change == "overlap":
        entries[0]["anchor"] = exclusions[0]["sku_anchor"] | {"quote": "苹果手机"}
        # Model the overlap after ordinary binding validation, which independently rejects forged anchors.
        bound = validate_bindings(sample()[2], definition, repo)
        bound = replace(bound, entries=(replace(bound.entries[0], anchor_refs=(repo.blocks[2].evidence_id,)),))
        with pytest.raises(ValueError):
            validate_exclusions(exclusions, definition, repo, bound)
        return
    with pytest.raises(ValueError):
        validate_exclusions(exclusions, definition, repo, validate_bindings(entries, definition, repo))


@pytest.mark.parametrize("specification", ["", "型号待确认", "型号 中文型号", "DEMO-P512 或 DEMO-P256"])
def test_unresolved_buyer_scope_cannot_authorize_exclusion(specification):
    from backend.app.criteria.page_exclusions import validate_exclusions

    definition, repo, entries, exclusions = sample(specification=specification)
    with pytest.raises(ValueError):
        validate_exclusions(exclusions, definition, repo, validate_bindings(entries, definition, repo))


def test_partial_parse_is_never_repaired_by_exclusions():
    from backend.app.criteria.page_exclusions import validate_exclusions

    definition, repo, entries, exclusions = sample(partial=True)
    with pytest.raises(ValueError):
        validate_exclusions(exclusions, definition, repo, validate_bindings(entries, definition, repo))


def test_frozen_exclusion_fingerprints_and_exact_fields_are_revalidated():
    definition, repo, entries, exclusions = sample()
    frozen = freeze(definition, repo, entries, exclusions)
    validated = validated_from_frozen(frozen, definition, repo)
    assert validated.exclusions[0].payload() == frozen["payload"]["exclusions"][0]
    for key in ("source_fingerprint", "anchor_fingerprint", "supplier", "evidence_refs"):
        bad = deepcopy(frozen)
        bad["payload"]["exclusions"][0][key] = [] if key == "evidence_refs" else "tampered"
        bad["digest"] = digest(bad["payload"])
        with pytest.raises(ValueError):
            validated_from_frozen(bad, definition, repo)
