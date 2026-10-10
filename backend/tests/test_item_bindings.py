"""Item anchors are independent from numeric evidence scopes."""

from dataclasses import replace

import pytest

from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.tests.test_checklists import definition


def sample():
    value = definition(1)
    value["items"][0]["name"] = "苹果手机"
    value["items"].append({"item_id": "shirt", "name": "T恤"})
    blocks = tuple(
        EvidenceBlock(
            evidence_id=str(i), document_id="doc", text=text, sheet="报价", cell=cell
        )
        for i, (cell, text) in enumerate(
            [
                ("A1", "苹果手机"),
                ("A2", "数量10台"),
                ("A3", "交期18自然日"),
                ("A4", "T恤"),
                ("A5", "数量20件"),
            ]
        )
    )
    parsed = ParseResult(
        document_id="doc",
        parser_version="parser",
        status="complete",
        blocks=blocks,
        total_sheets=1,
        processed_sheets=("报价",),
    )
    repo = EvidenceRepository(
        "case", 1, [Document("doc", "a" * 64, "quote", "A", "苹果手机", parsed)]
    )
    return ChecklistDefinition.model_validate(value), repo


def entry(repo, **updates):
    return dict(
        document_id="doc",
        source_sha256="a" * 64,
        parser_version="parser",
        item_id="rice",
        selector={
            "kind": "xlsx",
            "sheet": "报价",
            "start_row": 2,
            "end_row": 3,
            "start_column": 1,
            "end_column": 1,
        },
        anchor={"evidence_refs": [repo.blocks[0].evidence_id], "quote": "苹果手机"},
        identifier_class="category",
        **updates,
    )


def validate(body, definition, repo):
    from backend.app.criteria.item_bindings import validate_bindings

    return validate_bindings(body, definition, repo)


def test_anchor_does_not_expand_fact_scope_and_binding_is_immutable():
    spec, repo = sample()
    bound = validate([entry(repo)], spec, repo)
    assert bound.allowed_refs("rice", "A") == frozenset(
        b.evidence_id for b in repo.blocks[1:3]
    )
    assert (
        bound.assert_fact_scope("rice", [repo.blocks[1].evidence_id], "A").item_id
        == "rice"
    )
    with pytest.raises(ValueError, match="item_binding_scope"):
        bound.assert_fact_scope("rice", [repo.blocks[0].evidence_id], "A")
    with pytest.raises((AttributeError, TypeError)):
        bound.entries[0].item_id = "shirt"


@pytest.mark.parametrize(
    "changes",
    [
        {"item_id": "missing"},
        {"source_sha256": "b" * 64},
        {"parser_version": "old"},
        {"anchor": {"evidence_refs": ["invented"], "quote": "苹果手机"}},
        {"anchor": {"evidence_refs": [], "quote": "苹果手机"}},
        {
            "selector": {
                "kind": "xlsx",
                "sheet": "missing",
                "start_row": 1,
                "end_row": 2,
                "start_column": 1,
                "end_column": 1,
            }
        },
        {"supplier_sku": "invented", "identifier_class": "sku"},
    ],
)
def test_invalid_sources_and_identifiers_are_rejected(changes):
    spec, repo = sample()
    with pytest.raises(ValueError):
        validate([entry(repo) | changes], spec, repo)


def test_ranges_cannot_overlap_for_other_items_and_whole_requires_confirmation():
    spec, repo = sample()
    other = entry(repo) | {
        "item_id": "shirt",
        "identifier_class": "unknown",
        "anchor": {"evidence_refs": [repo.blocks[3].evidence_id], "quote": "T恤"},
    }
    with pytest.raises(ValueError, match="item_binding_overlap"):
        validate([entry(repo), other], spec, repo)
    with pytest.raises(ValueError):
        validate([entry(repo) | {"selector": {"kind": "whole"}}], spec, repo)
    with pytest.raises(ValueError, match="item_binding_conflict"):
        validate(
            [
                entry(repo)
                | {"selector": {"kind": "whole"}, "single_item_confirmed": True}
            ],
            spec,
            repo,
        )


def test_outside_rectangle_and_non_contiguous_anchor_join_are_rejected():
    spec, repo = sample()
    with pytest.raises(ValueError):
        validate(
            [
                entry(repo)
                | {
                    "selector": {
                        "kind": "xlsx",
                        "sheet": "报价",
                        "start_row": 1,
                        "end_row": 100,
                        "start_column": 1,
                        "end_column": 1,
                    }
                }
            ],
            spec,
            repo,
        )
    with pytest.raises(ValueError):
        validate(
            [
                entry(repo)
                | {
                    "anchor": {
                        "evidence_refs": [
                            repo.blocks[0].evidence_id,
                            repo.blocks[1].evidence_id,
                        ],
                        "quote": "苹果手机 数量10台",
                    }
                }
            ],
            spec,
            repo,
        )


def test_pdf_pages_and_whole_source_hash_identity():
    spec, repo = sample()
    parsed = replace(
        repo.documents[0].parsed,
        total_pages=2,
        total_sheets=None,
        processed_pages=(0, 1),
        processed_sheets=(),
        blocks=tuple(
            replace(b, sheet=None, cell=None, page_idx=0 if i < 3 else 1)
            for i, b in enumerate(repo.documents[0].parsed.blocks)
        ),
    )
    pdf = EvidenceRepository("case", 1, [replace(repo.documents[0], parsed=parsed)])
    bound = validate(
        [entry(pdf) | {"selector": {"kind": "pdf", "start_page": 1, "end_page": 1}}],
        spec,
        pdf,
    )
    assert len(bound.allowed_refs("rice")) == 3
    with pytest.raises(ValueError):
        validate(
            [
                entry(pdf)
                | {"selector": {"kind": "pdf", "start_page": 2, "end_page": 3}}
            ],
            spec,
            pdf,
        )


def test_unspecified_buyer_sku_does_not_turn_quantity_prose_into_model_requirement():
    spec, repo = sample()
    data = spec.model_dump(mode="json")
    data["items"][0]["specification"] = "采购数量10台；具体型号等待确认"
    original = repo.documents[0]
    parsed = replace(
        original.parsed,
        blocks=tuple(
            replace(b, text="苹果手机 型号 iPhone17") if n == 0 else b
            for n, b in enumerate(original.parsed.blocks)
        ),
    )
    source = EvidenceRepository(
        "case", 1, [replace(original, parsed=parsed, model="iPhone17")]
    )
    body = entry(source) | {
        "identifier_class": "sku",
        "supplier_sku": "iPhone17",
        "anchor": {
            "evidence_refs": [source.blocks[0].evidence_id],
            "quote": "苹果手机 型号 iPhone17",
        },
        "sku_anchor": {
            "evidence_refs": [source.blocks[0].evidence_id],
            "quote": "苹果手机 型号 iPhone17",
        },
        "selector": {
            "kind": "xlsx",
            "sheet": "报价",
            "start_row": 1,
            "end_row": 3,
            "start_column": 1,
            "end_column": 1,
        },
    }
    assert validate([body], data, source).entries[0].supplier_sku == "iPhone17"
    data["items"][0]["specification"] = "型号 iPhone16，数量10台"
    with pytest.raises(ValueError, match="item_binding_conflict"):
        validate([body], data, source)


def test_frozen_empty_binding_checks_definition_even_when_case_and_snapshot_match():
    from backend.app.criteria.item_bindings import validated_from_frozen
    from backend.app.evidence.repository import digest

    spec, repo = sample()
    payload = {
        "schema_version": 1,
        "case_id": repo.case_id,
        "snapshot_id": repo.snapshot_id,
        "checklist_version_id": "old",
        "checklist_digest": digest(None),
        "entries": [],
    }
    with pytest.raises(ValueError, match="item_binding_source_changed"):
        validated_from_frozen(
            {"digest": digest(payload), "payload": payload}, spec, repo
        )


def test_overlapping_same_item_cannot_supply_conflicting_identifier_classifications():
    spec, repo = sample()
    with pytest.raises(ValueError, match="item_binding_overlap"):
        validate(
            [entry(repo), entry(repo) | {"identifier_class": "unknown"}], spec, repo
        )


def test_upload_without_optional_product_identifier_can_be_associated_by_raw_name():
    spec, original = sample()
    repo = EvidenceRepository("case", 1, [replace(original.documents[0], model=None)])
    assert validate([entry(repo)], spec, repo).entries[0].item_id == "rice"


def test_positive_anchor_cannot_override_negated_item_inside_numeric_scope():
    spec, original = sample()
    document = original.documents[0]
    parsed = replace(
        document.parsed,
        blocks=tuple(
            replace(b, text="不是苹果手机，数量10台") if n == 1 else b
            for n, b in enumerate(document.parsed.blocks)
        ),
    )
    repo = EvidenceRepository("case", 1, [replace(document, parsed=parsed)])
    with pytest.raises(ValueError, match="item_binding_conflict"):
        validate([entry(repo)], spec, repo)


@pytest.mark.parametrize("label", ["型号", "款号", "SKU"])
def test_all_explicit_sku_labels_in_selected_range_keep_buyer_conflicts(label):
    spec, original = sample()
    data = spec.model_dump(mode="json")
    data["items"][0]["specification"] = "型号 iPhone16"
    document = original.documents[0]
    parsed = replace(
        document.parsed,
        blocks=tuple(
            replace(b, text=f"数量10台，{label}: iPhone17") if n == 1 else b
            for n, b in enumerate(document.parsed.blocks)
        ),
    )
    repo = EvidenceRepository("case", 1, [replace(document, parsed=parsed)])
    with pytest.raises(ValueError, match="item_binding_conflict"):
        validate([entry(repo)], data, repo)


def test_negative_certificate_is_not_a_negative_product_identity():
    spec, original = sample()
    document = original.documents[0]
    parsed = replace(document.parsed, blocks=tuple(
        replace(b, text="苹果手机 未提供质保证明") if n == 1 else b
        for n, b in enumerate(document.parsed.blocks)))
    repo = EvidenceRepository("case", 1, [replace(document, parsed=parsed)])
    assert validate([entry(repo)], spec, repo).entries
