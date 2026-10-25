"""Deterministic, independently located tax statements for bound money facts."""

import re
from decimal import Decimal

from openpyxl.utils.cell import coordinate_to_tuple

from ..evidence.repository import digest
from .item_bindings import (
    explicit_skus,
    identity_negated,
    pure_identity_block,
    unsupported_sku_declaration,
)
from .validation import (
    DURATION_START_EVENTS,
    NUMBER_PATTERN,
    compact,
    mentions,
    money_basis,
    source_span,
    strict_source_comparator,
)

_TAX_LABEL = r"(?:税费口径|税口径|是否含税|税费说明|税说明|含税说明)"
# Collection is deliberately broader than interpretation: an unfamiliar tax
# qualification must remain visible and unresolved rather than disappear.
_TAX_MARKER = re.compile(r"税")
_BARE_TAX = re.compile(r"(?:价格|报价)?(?:均)?(?:不含税|未含税|含税|税前|税后)[。.]?")
_PRICE_LABEL = re.compile(r"单价|价格|报价|售价|金额|总价|总额|预算|参考价")
# v13 remains frozen for historical facts. Only an entire, recognized metadata
# row can be excluded in v14; extra text or amounts remain conservatively priced.
_QUOTE_METADATA = re.compile(
    r"报价(?:日期|时间)\s*[:：=]?\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|报价有效期\s*[:：=]?\s*\d+(?:个)?(?:天|日|小时|月)"
    r"|报价编号\s*[:：=]?\s*[A-Za-z0-9_-]+"
)
_QUOTE_METADATA_LABEL = re.compile(r"报价(?:日期|时间|有效期|编号)[:：=]?")
MAX_TAX_SOURCE_GROUPS = 64
MAX_PRICE_SOURCE_GROUPS = 64
# Positive, server-owned context grammar. Arbitrary buyer labels/values cannot
# establish that a row is independent of the price being checked.
_COUNT_UNITS = {"台", "箱", "件", "个", "部", "套", "只", "双", "批"}
_INDEPENDENT_NUMERIC = {
    "数量": _COUNT_UNITS, "采购数量": _COUNT_UNITS,
    "存储容量": {"GB", "TB", "MB"}, "内存容量": {"GB", "MB"}, "运行内存": {"GB", "MB"},
}
_INDEPENDENT_CONDITIONS = {"全新未激活", "全新未拆封", "全新", "未激活", "二手", "翻新", "已激活"}
_INDEPENDENT_MATERIALS = {"质保承诺书", "检测报告", "合格证", "认证证书", "产品说明书", "检验报告"}
_UNIT_PRICE_LABEL = r"(?:不含税单价|未含税单价|税前单价|含税单价|采购单价|单价)"
_CURRENCY = r"(?:人民币|CNY|RMB|元|￥|¥|美元|USD|US\$|欧元|EUR|€)"
_UNIT_PRICE = re.compile(
    rf"\s*(?P<label>{_UNIT_PRICE_LABEL})(?:\s*(?P=label))?\s*[:：=]?\s*"
    rf"(?:(?P<before>{_CURRENCY})\s*)?(?P<amount>\d{{1,12}}(?:\.\d{{1,6}})?)\s*"
    rf"(?:(?P<after>{_CURRENCY})\s*)?"
    r"[/／]\s*(?P<unit>台|箱|件|个|部|套|只|双|批|公斤|千克|kg|吨|米|平方米)\s*"
    r"(?:[（(]?(?:含税|不含税|未含税|税前|税后)[）)]?)?[。.]?\s*", re.IGNORECASE)


def _price_signature(repo, row):
    """Interpret a whole simple price span, without buyer defaults or model help."""
    if len(row) > 4:
        return None
    text = " ".join(block.text for block in row)
    try:
        source_span(repo, [block.evidence_id for block in row], text)
    except ValueError:
        return None
    matched = _UNIT_PRICE.fullmatch(text)
    if not matched:
        return None
    # Currency is derived from the same complete tokens accepted by the grammar.
    currency, _, _ = money_basis(" ".join(filter(None, (matched["before"], matched["after"]))), "")
    _, _, tax = money_basis(text, "")
    if currency is None or tax == "unspecified":
        return None
    return Decimal(matched["amount"]), currency, matched["unit"], tax


def _unrelated_metric(text, definition, item):
    """Recognize a whole other metric; arbitrary notes never inherit its label."""
    if _PRICE_LABEL.search(text):
        return False
    for criterion in definition.criteria:
        if criterion.item_id != item.item_id or criterion.rule.type == "money":
            continue
        rule = criterion.rule
        label = re.escape(criterion.name)
        heading = rf"{label}(?:\s*{label})?\s*[:：]?\s*"
        if not re.match(heading, text):
            continue
        if rule.type in {"numeric", "duration"}:
            if rule.type == "numeric" and rule.unit not in _INDEPENDENT_NUMERIC.get(criterion.name, set()):
                continue
            if rule.type == "duration" and (criterion.name not in {"交付期限", "交货期限", "交货期"}
                                            or rule.start_event not in DURATION_START_EVENTS):
                continue
            units = ([rule.unit] if rule.type == "numeric" else
                     {"natural_days": ["自然日", "天", "日"], "working_days": ["工作日"],
                      "months": ["月"]}[rule.calendar])
            if any(strict_source_comparator(text, criterion, item, unit,
                       start_event=getattr(rule, "start_event", None), bound_aliases=True) != "unknown"
                   for unit in units):
                return True
        elif rule.type == "enum":
            if criterion.name == "商品状态" and re.fullmatch(
                    heading + r"(?:" + "|".join(map(re.escape, _INDEPENDENT_CONDITIONS)) + r")[。.]?", text):
                return True
        elif rule.type == "presence":
            if criterion.name in _INDEPENDENT_MATERIALS and re.fullmatch(
                    heading + r"(?:已附|已提供|提供)\s*(?:本采购品目对应的)?" + label + r"[。.]?", text):
                return True
    return False


def _safe_price_context(repo, row, entry, definition, item):
    text = " ".join(block.text for block in row).strip()
    if not text:
        return True
    try:
        source_span(repo, [block.evidence_id for block in row], text)
    except ValueError:
        return False
    return (_price_signature(repo, row) is not None or _is_quote_metadata(row)
            or pure_identity_block(text, item.name, entry.supplier_sku)
            or _tax_basis(text) != "unspecified" or _unrelated_metric(text, definition, item))


def _consistent_prices(repo, price_rows, scoped_rows, entry, main_refs, definition, item):
    """Require every price row and its confirmed identity to independently agree."""
    if len(price_rows) < 2 or not any(
            price_entry == entry and main_refs <= {b.evidence_id for b in row}
            for price_entry, row in price_rows):
        return False
    signatures = [_price_signature(repo, row) for _, row in price_rows]
    if None in signatures or len(set(signatures)) != 1:
        return False
    if any(not _safe_price_context(repo, row, source_entry, definition, item)
           for source_entry, row in scoped_rows):
        return False
    for price_entry, _ in price_rows:
        if (price_entry.identifier_class not in {"category", "sku"}
                or price_entry.supplier_sku != entry.supplier_sku):
            return False
        text = "\n".join(b.text for source_entry, row in scoped_rows if source_entry == price_entry for b in row)
        if (identity_negated(text, item.name) or unsupported_sku_declaration(text)
                or re.search(r"另一型号|其他型号|不属于本次", text)
                or any(other.item_id != item.item_id and mentions(text, other.name) for other in definition.items)):
            return False
        skus = explicit_skus(text)
        expected = {price_entry.supplier_sku.casefold()} if price_entry.supplier_sku else set()
        if skus and skus != expected:
            return False
    return True


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


def _is_quote_metadata(row):
    # Keep cell and word boundaries: deleting whitespace could absorb an extra
    # amount such as "Q20261019 30 CNY" into an otherwise valid identifier.
    parts = [block.text.strip() for block in row if block.text.strip()]
    if len(parts) == 2 and _QUOTE_METADATA_LABEL.fullmatch(parts[0]):
        return bool(_QUOTE_METADATA.fullmatch(parts[0].rstrip(":：=") + ":" + parts[1]))
    return len(parts) == 1 and bool(_QUOTE_METADATA.fullmatch(parts[0]))


def money_tax_sources(repo, bound, entry, blocks, definition, item, main_tax, *, quote_metadata=False,
                      consistent_prices=False):
    """Scan all confirmed ranges for this supplier/item, never model-selected rows.

    Only a complete, contiguous Excel row in the same confirmed range as the
    supplier/item's unique price can establish applicability. v15 also recognizes
    complete equivalent prices carrying their own tax basis. Other independently
    bound prices and tax sources remain visible for a human to resolve.
    """
    main_refs = {block.evidence_id for block in blocks}
    main_text = re.sub(r"\s+", "", " ".join(block.text for block in blocks))
    main_tax_conflicted = (bool(re.search(r"不含税|未含税|税前|excluded", main_text, re.IGNORECASE))
                           and bool(re.search(r"(?<!不)(?<!未)含税|税后|included", main_text, re.IGNORECASE)))
    scoped_rows = []
    for source_entry in bound.bindings.entries:
        if source_entry.item_id != item.item_id or source_entry.supplier != entry.supplier:
            continue
        source_blocks = repo.read(list(source_entry.evidence_refs))
        if any(block.document_id != source_entry.document_id or block.source_hash != source_entry.source_sha256
               or block.parser_version != source_entry.parser_version for block in source_blocks):
            raise ValueError("item_binding_source_changed")
        if any(block.role not in {"quote", "spec"} or block.supplier != entry.supplier for block in source_blocks):
            raise ValueError("supplier_scope")
        scoped_rows.extend((source_entry, row) for row in _rows(source_blocks))
    main_rows = [row for source_entry, row in scoped_rows if source_entry == entry]
    price_rows = []
    for source_entry, row in scoped_rows:
        text = " ".join(block.text for block in row)
        if _PRICE_LABEL.search(text) and re.search(NUMBER_PATTERN, text):
            if quote_metadata and _is_quote_metadata(row):
                continue
            price_rows.append((source_entry, row))
    unique_price = (len(price_rows) == 1
                    and price_rows[0][0] == entry
                    and main_refs <= {block.evidence_id for block in price_rows[0][1]}
                    and len(re.findall(NUMBER_PATTERN, " ".join(b.text for b in price_rows[0][1]))) == 1)
    equivalent_prices = (consistent_prices and _consistent_prices(
        repo, price_rows, scoped_rows, entry, main_refs, definition, item))
    price_sources = []
    for price_entry, row in price_rows:
        refs = [block.evidence_id for block in row]
        if set(refs) <= main_refs or (unique_price or equivalent_prices) and main_refs <= set(refs):
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
                                  "item_binding_entry_digest": digest(price_entry.payload())})
    all_main_text = "\n".join(block.text for row in main_rows for block in row)
    mixed_items = any(other.item_id != item.item_id and mentions(all_main_text, other.name)
                      for other in definition.items)
    same_sheet = (all(block.sheet and block.cell for block in blocks)
                  and len({block.sheet for block in blocks}) == 1)
    sources, seen = [], set()
    for source_entry, row in scoped_rows:
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
        # v15: a complete equivalent price carries its own explicit tax basis.
        # This never assigns a separate note to another document's price.
        signature = _price_signature(repo, row) if equivalent_prices else None
        if signature is not None:
            resolved = True
            basis = signature[3]
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
            "price_sources": price_sources,
            "price_scope_state": "resolved" if unique_price or equivalent_prices else "unclear"}
