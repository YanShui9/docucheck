"""Explicit confirmation for single-item synthetic integration documents."""

from backend.app.api.auth import Principal
from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.item_binding_repository import ItemBindingRepository


def confirm_xlsx_item_ranges(engine, case_id, snapshot_id, confirmed):
    """Explicitly confirm each synthetic item row; never infer production bindings."""
    from openpyxl.utils.cell import coordinate_to_tuple

    repo = load_agent_snapshot(engine, case_id, snapshot_id)
    version = confirmed["confirmed_version"]
    entries = []
    for item in version["definition"]["items"]:
        for doc in repo.documents:
            if doc.supplier is None:
                continue
            for block in repo.blocks:
                if block.document_id != doc.document_id or item["name"] not in block.text:
                    continue
                row, column = coordinate_to_tuple(block.cell)
                entries.append({"document_id": doc.document_id, "source_sha256": doc.source_hash,
                    "parser_version": doc.parsed.parser_version, "item_id": item["item_id"],
                    "selector": {"kind": "xlsx", "sheet": block.sheet, "start_row": row, "end_row": row,
                                 "start_column": column, "end_column": column},
                    "anchor": {"evidence_refs": [block.evidence_id], "quote": block.text}, "identifier_class": "category"})
    store = ItemBindingRepository(engine)
    revision = store.get(case_id, snapshot_id)["binding_revision"]
    return store.put(case_id, snapshot_id, entries, actor=Principal("local", "local").key,
        checklist_version_id=version["id"], expected_revision=repo.revision,
        expected_checklist_revision=confirmed["checklist_revision"], expected_binding_revision=revision,
        confirmed=True, idempotency_key="confirm-item-scope-" + version["id"], reason="人工核对合成表格各品目范围")


def confirm_single_item_bindings(engine, case_id, snapshot_id, confirmed):
    repo = load_agent_snapshot(engine, case_id, snapshot_id)
    version = confirmed["confirmed_version"]
    item = version["definition"]["items"][0]
    entries = []
    for doc in repo.documents:
        if doc.supplier is None:
            continue
        block = next(b for b in repo.blocks if b.document_id == doc.document_id and item["name"] in b.text)
        entries.append({"document_id": doc.document_id, "source_sha256": doc.source_hash,
                        "parser_version": doc.parsed.parser_version, "item_id": item["item_id"],
                        "selector": {"kind": "whole"}, "single_item_confirmed": True,
                        "anchor": {"evidence_refs": [block.evidence_id], "quote": block.text},
                        "identifier_class": "category"})
    return ItemBindingRepository(engine).put(
        case_id, snapshot_id, entries, actor=Principal("local", "local").key,
        checklist_version_id=version["id"], expected_revision=repo.revision,
        expected_checklist_revision=confirmed["checklist_revision"],
        expected_binding_revision=0, confirmed=True, idempotency_key="confirm-item-scope",
        reason="人工核对合成单品原文")
