"""Deterministic, independently located tax statements for bound money facts."""

import re

from openpyxl.utils.cell import coordinate_to_tuple

from ..evidence.repository import digest
from .validation import NUMBER_PATTERN, compact, mentions, source_span

_TAX_LABEL = r"(?:税费口径|税口径|是否含税|税费说明|税说明|含税说明)"
# Collection is deliberately broader than interpretation: an unfamiliar tax
# qualification must remain visible and unresolved rather than disappear.
_TAX_MARKER = re.compile(r"税")
_BARE_TAX = re.compile(r"(?:价格|报价)?(?:均)?(?:不含税|未含税|含税|税前|税后)[。.]?")
_PRICE_LABEL = re.compile(r"单价|价格|报价|售价|金额|总价|总额|预算|参考价")
MAX_TAX_SOURCE_GROUPS = 64
MAX_PRICE_SOURCE_GROUPS = 64


def _rows(blocks):
    rows = {}
    for block in blocks:
        key = ((block.document_id, block.sheet, coordinate_to_tuple(block.cell)[0])
               if block.sheet and block.cell else (block.document_id, block.evidence_id))
        rows.setdefault(key, []).append(block)
    for row in rows.values():
        if all(block.sheet and block.cell for block in row):
            row.sort(key=lambda block: coordinate_to_tuple(block.cell))
        yield row


def _tax_basis(text):
    text = re.sub(r"\s+", "", text)
    matched = re.fullmatch(rf"{_TAX_LABEL}[:：=]?(?:为|是)?(不含税|未含税|含税|税前|税后)[。.]?", text)
    if matched:
        return "included" if matched[1] in {"含税", "税后"} else "excluded"
    yes_no = re.fullmatch(r"是否含税[:：=]?(是|否)[。.]?", text)
    if yes_no:
        return "included" if yes_no[1] == "是" else "excluded"
    if _BARE_TAX.fullmatch(text):
        return "excluded" if re.search(r"不含税|未含税|税前", text) else "included"
    return "unspecified"


def money_tax_sources(repo, bound, entry, blocks, definition, item, main_tax):
    """Scan all confirmed ranges for this supplier/item, never model-selected rows.

    Only a complete, contiguous Excel row in the same confirmed range as one
    unique price can establish applicability. Other independently bound sources
    remain visible but require a human to establish their relationship.
    """
    main_refs = {block.evidence_id for block in blocks}
    main_text = re.sub(r"\s+", "", " ".join(block.text for block in blocks))
    main_tax_conflicted = (bool(re.search(r"不含税|未含税|税前|excluded", main_text, re.IGNORECASE))
                           and bool(re.search(r"(?<!不)(?<!未)含税|税后|included", main_text, re.IGNORECASE)))
    main_rows = list(_rows(repo.read(list(entry.evidence_refs))))
    price_rows = [row for row in main_rows
                  if _PRICE_LABEL.search(" ".join(block.text for block in row))
                  and re.search(NUMBER_PATTERN, " ".join(block.text for block in row))]
    unique_price = (len(price_rows) == 1
                    and main_refs <= {block.evidence_id for block in price_rows[0]}
                    and len(re.findall(NUMBER_PATTERN, " ".join(b.text for b in price_rows[0]))) == 1)
    price_sources = []
    for row in price_rows:
        refs = [block.evidence_id for block in row]
        if set(refs) <= main_refs or unique_price and main_refs <= set(refs):
            continue
        text = " ".join(block.text for block in row)
        contiguous = len(row) <= 4
        if contiguous:
            try:
                source_span(repo, refs, text)
            except ValueError:
                contiguous = False
        for group in [row] if contiguous else [[block] for block in row]:
            if len(price_sources) >= MAX_PRICE_SOURCE_GROUPS:
                raise ValueError("money_price_source_budget")
            group_refs = [block.evidence_id for block in group]
            group_quote = " ".join(block.text for block in group)
            source_span(repo, group_refs, group_quote)
            price_sources.append({"evidence_refs": group_refs, "quote": compact(group_quote),
                                  "item_binding_entry_digest": digest(entry.payload())})
    all_main_text = "\n".join(block.text for row in main_rows for block in row)
    mixed_items = any(other.item_id != item.item_id and mentions(all_main_text, other.name)
                      for other in definition.items)
    same_sheet = (all(block.sheet and block.cell for block in blocks)
                  and len({block.sheet for block in blocks}) == 1)
    sources, seen = [], set()
    for source_entry in bound.bindings.entries:
        if source_entry.item_id != item.item_id or source_entry.supplier != entry.supplier:
            continue
        source_blocks = repo.read(list(source_entry.evidence_refs))
        if any(block.document_id != source_entry.document_id or block.source_hash != source_entry.source_sha256
               or block.parser_version != source_entry.parser_version for block in source_blocks):
            raise ValueError("item_binding_source_changed")
        if any(block.role not in {"quote", "spec"} or block.supplier != entry.supplier for block in source_blocks):
            raise ValueError("supplier_scope")
        for row in _rows(source_blocks):
            text = " ".join(block.text for block in row)
            normalized = re.sub(r"\s+", "", text)
            if not (_TAX_MARKER.search(normalized) or _BARE_TAX.fullmatch(normalized)):
                continue
            refs = [block.evidence_id for block in row]
            if set(refs) <= main_refs:
                continue
            if tuple(refs) in seen:
                continue
            seen.add(tuple(refs))
            # A broken/oversized row is not silently omitted or joined. Preserve
            # each original block and keep the whole statement unresolved.
            contiguous = len(row) <= 4
            if contiguous:
                try:
                    source_span(repo, refs, text)
                except ValueError:
                    contiguous = False
            groups = [row] if contiguous else [[block] for block in row]
            basis = _tax_basis(text)
            resolved = (contiguous and basis != "unspecified" and unique_price and same_sheet
                        and not mixed_items and entry.identifier_class in {"category", "sku"}
                        and source_entry == entry and row[0].sheet == blocks[0].sheet
                        and all(block.sheet and block.cell for block in row))
            for group in groups:
                if len(sources) >= MAX_TAX_SOURCE_GROUPS:
                    raise ValueError("money_tax_source_budget")
                group_refs = [block.evidence_id for block in group]
                group_quote = " ".join(block.text for block in group)
                source_span(repo, group_refs, group_quote)
                sources.append({"evidence_refs": group_refs, "quote": compact(group_quote),
                                "tax_basis": basis if contiguous else "unspecified",
                                "scope_state": "resolved" if resolved else "unclear",
                                "item_binding_entry_digest": digest(source_entry.payload())})
    unclear = main_tax_conflicted or any(source["scope_state"] != "resolved" for source in sources)
    bases = {source["tax_basis"] for source in sources if source["scope_state"] == "resolved"}
    if main_tax != "unspecified":
        bases.add(main_tax)
    conflict = len(bases) > 1
    effective = next(iter(bases)) if len(bases) == 1 and not unclear else main_tax
    return {"tax_sources": sources, "tax_scope_state": "unclear" if unclear else "resolved",
            "tax_conflict": conflict, "effective_tax_basis": effective,
            "price_sources": price_sources, "price_scope_state": "resolved" if unique_price else "unclear"}
