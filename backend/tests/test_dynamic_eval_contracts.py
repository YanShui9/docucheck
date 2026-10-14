"""Dynamic evaluation labels are frozen independently of product decisions."""

import re
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from importlib import import_module, util
from io import BytesIO
from xml.etree.ElementTree import fromstring
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from openpyxl import Workbook
from pydantic import ValidationError


def contracts():
    assert util.find_spec("evals.dynamic.contracts") is not None, "dynamic contracts missing"
    return import_module("evals.dynamic.contracts")


def fixture_data():
    texts = {
        "requirements.xlsx": ["Phone model DEMO-P256", "quantity 10", "capacity 256", "certificate attached"],
        "quote.xlsx": ["Phone model DEMO-P256", "quantity 10", "capacity 256", "certificate promised"],
        "spec.xlsx": ["Phone model DEMO-P256", "quantity 10", "capacity 128", "certificate promised"],
    }
    originals = {}
    for filename, rows in texts.items():
        book = Workbook()
        book.properties.created = book.properties.modified = datetime(2026, 10, 11, tzinfo=UTC)
        book.active.title = "Data"
        for text in rows:
            book.active.append([text])
        stream = BytesIO()
        book.save(stream)
        stable = BytesIO()
        with ZipFile(stream) as source, ZipFile(stable, "w", compression=ZIP_DEFLATED) as target:
            for name in sorted(source.namelist()):
                info = ZipInfo(name, date_time=(2026, 10, 11, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                content = source.read(name)
                if name == "docProps/core.xml":
                    content = re.sub(rb"(<dcterms:modified\b[^>]*>).*?(</dcterms:modified>)",
                                     rb"\g<1>2026-10-11T00:00:00Z\g<2>", content)
                target.writestr(info, content)
        originals[filename] = stable.getvalue()

    def anchor(filename, row):
        return {"document": filename,
                "role": "requirement" if filename == "requirements.xlsx" else filename.split(".")[0],
                "supplier_handle": None if filename == "requirements.xlsx" else "vendor-a",
                "item_id": "phone", "text": texts[filename][row - 1], "sheet": "Data", "cell": f"A{row}"}

    scope = {"sheet": "Data", "cell_range": "A1:A4"}
    manifest = {
        "schema_version": 1, "protocol_id": "dynamic-dev-v1", "pack_id": "dyn-fixture",
        "data_class": "ai_authored_seen_development", "category": "phone",
        "source_family_ids": ["self-authored-phone"], "supplier_handles": ["vendor-a"],
        "authors": ["AI fixture author"], "reviewers": ["AI fixture review"],
        "documents": [{"filename": filename, "role": anchor(filename, 1)["role"],
                       "supplier_handle": anchor(filename, 1)["supplier_handle"],
                       "sha256": sha256(data).hexdigest(), "bytes": len(data), "pages": 0}
                      for filename, data in originals.items()],
        "checklist_definition": {
            "items": [{"item_id": "phone", "name": "Phone", "specification": "model DEMO-P256"}],
            "criteria": [{"criterion_id": name, "item_id": "phone", "name": name,
                          "rule": rule, "source": {"kind": "user_defined", "reason": "frozen buyer input"}}
                         for name, rule in [
                             ("quantity", {"type": "numeric", "operator": "eq", "value": "10", "unit": "unit"}),
                             ("capacity", {"type": "numeric", "operator": "gte", "value": "256", "unit": "GB"}),
                             ("certificate", {"type": "presence", "description": "attached certificate"})]]},
        "buyer_anchors": {name: anchor("requirements.xlsx", row)
                          for name, row in [("quantity", 2), ("capacity", 3), ("certificate", 4)]},
        "bindings": [{"document": filename, "item_id": "phone", "sku": "DEMO-P256",
                      "scope": scope, "anchor": anchor(filename, 1), "sku_anchor": anchor(filename, 1)}
                     for filename in originals],
    }
    rows = []
    for criterion, status, row in [("quantity", "满足", 2), ("capacity", "资料冲突", 3),
                                   ("certificate", "证据不足", 4)]:
        groups = [{"group_id": "buyer", "purpose": "buyer_baseline",
                   "alternatives": [anchor("requirements.xlsx", row)]}]
        if status != "证据不足":
            groups.append({"group_id": "quote", "purpose": "supplier_claim",
                           "alternatives": [anchor("quote.xlsx", row)]})
        if status == "资料冲突":
            groups.append({"group_id": "spec", "purpose": "supplier_claim",
                           "alternatives": [anchor("spec.xlsx", row)]})
        rows.append({"pack_id": "dyn-fixture", "supplier_handle": "vendor-a", "item_id": "phone",
                     "criterion_id": criterion, "expected_processing": "complete", "expected_business": status,
                     "expected_formal": True, "expected_needs_review": False,
                     "reason": "Independently written fixture expectation", "rule_basis": "same model and units",
                     "buyer_baseline": texts["requirements.xlsx"][row - 1], "source_version": "frozen-v1",
                     "evidence_groups": groups,
                     "required_coverage": [{"document": filename, "item_id": "phone", "scope": scope}
                                           for filename in ["quote.xlsx", "spec.xlsx"]]})
    gold = {"schema_version": 1, "protocol_id": "dynamic-dev-v1", "pack_id": "dyn-fixture",
            "data_class": "ai_authored_seen_development", "author": "AI fixture author",
            "reviewer": "AI fixture review", "review_state": "ai_reviewed_development", "cells": rows}
    return manifest, gold, originals


def test_contracts_expand_enabled_dynamic_primary_keys():
    module = contracts()
    manifest, gold, _ = fixture_data()
    model = module.Manifest.model_validate(manifest)
    labels = module.Gold.model_validate(gold)
    module.validate_pair(model, labels)
    assert set(model.primary_keys()) == {("dyn-fixture", "vendor-a", "phone", name)
                                         for name in ("quantity", "capacity", "certificate")}


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "extra", "supplier", "item", "pack"])
def test_gold_has_exactly_one_cell_for_every_frozen_key(mutation):
    module = contracts()
    manifest, gold, _ = fixture_data()
    if mutation == "duplicate":
        gold["cells"].append(deepcopy(gold["cells"][0]))
    elif mutation == "missing":
        gold["cells"].pop()
    elif mutation == "extra":
        cell = deepcopy(gold["cells"][0])
        cell["criterion_id"] = "extra"
        gold["cells"].append(cell)
    else:
        field = {"supplier": "supplier_handle", "item": "item_id", "pack": "pack_id"}[mutation]
        gold["cells"][0][field] = "unknown"
    with pytest.raises((ValidationError, ValueError)):
        module.validate_pair(module.Manifest.model_validate(manifest), module.Gold.model_validate(gold))


@pytest.mark.parametrize("mutation", ["boolean_bytes", "boolean_schema", "unsafe_filename", "unknown_supplier", "gold_leak",
                                     "invalid_location", "unknown_binding_item", "missing_sku_anchor"])
def test_manifest_rejects_unsafe_or_ambiguous_business_inputs(mutation):
    module = contracts()
    manifest, _, _ = fixture_data()
    if mutation == "boolean_bytes":
        manifest["documents"][0]["bytes"] = True
    elif mutation == "boolean_schema":
        manifest["schema_version"] = True
    elif mutation == "unsafe_filename":
        manifest["documents"][0]["filename"] = "../gold/labels.xlsx"
    elif mutation == "unknown_supplier":
        manifest["documents"][1]["supplier_handle"] = "vendor-b"
    elif mutation == "gold_leak":
        manifest["gold"] = {"satisfied": True}
    elif mutation == "invalid_location":
        manifest["bindings"][0]["anchor"]["page"] = True
    elif mutation == "unknown_binding_item":
        manifest["bindings"][0]["item_id"] = "fruit"
    else:
        manifest["bindings"][0]["sku_anchor"] = None
    with pytest.raises(ValidationError):
        module.Manifest.model_validate(manifest)


@pytest.mark.parametrize("mutation", ["buyer_proves_supplier", "one_conflict_group", "wrong_anchor_item",
                                     "missing_absence_scope", "wrong_role", "partial_gold"])
def test_gold_requires_separate_supplier_support_and_complete_absence_scopes(mutation):
    module = contracts()
    manifest, gold, _ = fixture_data()
    if mutation == "buyer_proves_supplier":
        gold["cells"][0]["evidence_groups"][1]["alternatives"] = deepcopy(
            gold["cells"][0]["evidence_groups"][0]["alternatives"])
    elif mutation == "one_conflict_group":
        gold["cells"][1]["evidence_groups"].pop()
    elif mutation == "wrong_anchor_item":
        gold["cells"][0]["evidence_groups"][0]["alternatives"][0]["item_id"] = "fruit"
    elif mutation == "missing_absence_scope":
        gold["cells"][2]["required_coverage"].pop()
    elif mutation == "wrong_role":
        gold["cells"][0]["evidence_groups"][1]["alternatives"][0]["role"] = "requirement"
    else:
        gold["cells"][2]["expected_processing"] = "partial"
    with pytest.raises((ValidationError, ValueError)):
        module.validate_pair(module.Manifest.model_validate(manifest), module.Gold.model_validate(gold))


def test_requirement_evidence_does_not_need_supplier_document_binding():
    module = contracts()
    manifest, gold, _ = fixture_data()
    manifest["bindings"] = [b for b in manifest["bindings"] if b["document"] != "requirements.xlsx"]
    module.validate_pair(module.Manifest.model_validate(manifest), module.Gold.model_validate(gold))


def test_two_identical_conflict_groups_cannot_substitute_for_opposing_evidence():
    module = contracts()
    manifest, gold, _ = fixture_data()
    gold["cells"][1]["evidence_groups"][2]["alternatives"] = deepcopy(
        gold["cells"][1]["evidence_groups"][1]["alternatives"])
    with pytest.raises((ValidationError, ValueError)):
        module.validate_pair(module.Manifest.model_validate(manifest), module.Gold.model_validate(gold))


def test_model_suffix_cannot_prove_the_bound_sku():
    module = contracts()
    manifest, _, _ = fixture_data()
    manifest["bindings"][1]["sku_anchor"]["text"] = "Phone model DEMO-P256PRO"
    with pytest.raises(ValidationError, match="missing_sku_anchor"):
        module.Manifest.model_validate(manifest)


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong_item", "supplier_document", "outside_pdf_pages"])
def test_buyer_anchors_are_business_inputs_for_each_enabled_criterion(mutation):
    module = contracts()
    manifest, _, _ = fixture_data()
    if mutation == "missing":
        manifest["buyer_anchors"].pop("quantity")
    elif mutation == "extra":
        manifest["buyer_anchors"]["unrequested"] = deepcopy(manifest["buyer_anchors"]["quantity"])
    elif mutation == "wrong_item":
        manifest["buyer_anchors"]["quantity"]["item_id"] = "fruit"
    elif mutation == "supplier_document":
        manifest["buyer_anchors"]["quantity"] = deepcopy(manifest["bindings"][1]["anchor"])
    else:
        document = manifest["documents"][0]
        document.update(filename="requirements.pdf", pages=1)
        manifest["bindings"] = [b for b in manifest["bindings"] if b["document"] != "requirements.xlsx"]
        for anchor in manifest["buyer_anchors"].values():
            anchor.update(document="requirements.pdf", page=2)
            anchor.pop("sheet")
            anchor.pop("cell")
    with pytest.raises(ValidationError):
        module.Manifest.model_validate(manifest)


@pytest.mark.parametrize("mutation", ["intersection", "whitespace", "nfkc", "subset", "text_substring"])
def test_conflict_alternatives_cannot_share_a_supplier_claim(mutation):
    module = contracts()
    manifest, gold, _ = fixture_data()
    quote = gold["cells"][1]["evidence_groups"][1]
    spec = gold["cells"][1]["evidence_groups"][2]
    shared = deepcopy(quote["alternatives"][0])
    if mutation == "whitespace":
        shared["text"] = "capacity\n \t256"
    elif mutation == "nfkc":
        shared["text"] = "ｃａｐａｃｉｔｙ ２５６"
    elif mutation == "subset":
        quote["alternatives"].append(deepcopy(spec["alternatives"][0]))
        shared = deepcopy(spec["alternatives"][0])
    elif mutation == "text_substring":
        shared["text"] = "256"
    spec["alternatives"].append(shared)
    with pytest.raises((ValidationError, ValueError), match="conflict_requires_distinct_claims"):
        module.validate_pair(module.Manifest.model_validate(manifest), module.Gold.model_validate(gold))


def test_distinct_conflicting_facts_can_share_one_pdf_page():
    module = contracts()
    _, gold, _ = fixture_data()
    cell = gold["cells"][1]
    for group, text in zip(cell["evidence_groups"][1:], ("capacity 256", "capacity 128"), strict=True):
        anchor = group["alternatives"][0]
        anchor.update(document="response.pdf", role="spec", page=1, text=text)
        anchor.pop("sheet")
        anchor.pop("cell")
    validated = module.GoldCell.model_validate(cell)
    assert len(validated.evidence_groups) == 3


def test_fixture_freezes_internal_xlsx_metadata_before_hashing():
    _, _, originals = fixture_data()
    for original in originals.values():
        with ZipFile(BytesIO(original)) as archive:
            metadata = fromstring(archive.read("docProps/core.xml"))
        for field in ("created", "modified"):
            assert metadata.find(f"{{http://purl.org/dc/terms/}}{field}").text == "2026-10-11T00:00:00Z"


def test_bbox_alias_does_not_make_one_text_claim_independent():
    module = contracts()
    _, gold, _ = fixture_data()
    cell = gold["cells"][1]
    for group, bbox in zip(cell["evidence_groups"][1:], (None, [10.0, 20.0, 100.0, 40.0]), strict=True):
        anchor = group["alternatives"][0]
        anchor.update(document="response.pdf", role="spec", page=1, text="capacity 256", bbox=bbox)
        anchor.pop("sheet")
        anchor.pop("cell")
    with pytest.raises(ValidationError, match="conflict_requires_distinct_claims"):
        module.GoldCell.model_validate(cell)
