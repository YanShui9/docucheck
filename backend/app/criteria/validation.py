"""Finite, source-bound checks for requirement definitions and evidence spans."""

import re
from decimal import Decimal

from openpyxl.utils.cell import coordinate_to_tuple

from ..evidence.repository import EvidenceRepository
from .models import ChecklistDefinition, Criterion, ProcurementItem

NUMBER_PATTERN = r"(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])"
COMPLEX = re.compile(r"(?:大约|约)\s*-?\d|左右|可选|任选|选配|可升级|或者|或|且|如果|若|除非|特殊|"
                     r"可能|计划|预计|拟提供|拟提交|拟交付|拟交货|不完整|部分|混纺|混合|"
                     r"无需|不要求|无要求|不作要求|不适用|仅供|示例|参考|建议|"
                     r"depending|optional|approximately|another model", re.IGNORECASE)
SOURCE_RELATIONSHIP_DETAILS = frozenset({
    "mixed_document", "mixed_sheet", "mixed_role", "mixed_supplier", "mixed_model",
    "non_tabular", "invalid_cell", "different_row", "non_adjacent_columns",
})


class EvidenceRelationshipError(ValueError):
    """Keep the stable rejection code while carrying a finite structural reason."""

    def __init__(self, detail):
        super().__init__("evidence_relationship")
        self.relationship_detail = detail


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _structural_quote(text: str) -> str:
    return "\n".join(compact(line) for line in text.strip().splitlines())


def source_span(repo: EvidenceRepository, refs: list[str], quote: str):
    """Read one paragraph or consecutive horizontal cells, never guessed joins."""
    if not refs or len(set(refs)) != len(refs):
        raise ValueError("evidence_scope")
    selected = repo.read(refs)
    if len(selected) > 1:
        first = selected[0]
        for attribute, detail in (("document_id", "mixed_document"), ("role", "mixed_role"),
                                  ("supplier", "mixed_supplier"), ("model", "mixed_model")):
            if any(getattr(b, attribute) != getattr(first, attribute) for b in selected):
                raise EvidenceRelationshipError(detail)
        if any(not b.sheet or not b.cell for b in selected):
            raise EvidenceRelationshipError("non_tabular")
        if any(b.sheet != first.sheet for b in selected):
            raise EvidenceRelationshipError("mixed_sheet")
        try:
            selected = sorted(selected, key=lambda b: coordinate_to_tuple(b.cell))
            positions = [coordinate_to_tuple(b.cell) for b in selected]
        except ValueError as exc:
            raise EvidenceRelationshipError("invalid_cell") from exc
        if any(row != positions[0][0] for row, _ in positions):
            raise EvidenceRelationshipError("different_row")
        if any(col != positions[0][1] + n for n, (_, col) in enumerate(positions)):
            raise EvidenceRelationshipError("non_adjacent_columns")
    text = " ".join(block.text for block in selected)
    if not compact(quote) or compact(quote) not in compact(text):
        raise ValueError("evidence_quote")
    return selected, text


def mentions(text: str, value: str) -> bool:
    if not value:
        return False
    if re.fullmatch(r"[A-Za-z0-9._ -]+", value):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])",
                         text, re.IGNORECASE) is not None
    return value in text


def negated(text: str, value: str) -> bool:
    before = r"(?:未提供|不提供|不具备|未附|没有|缺少|缺失|不采用|不使用|不含|不是|非|无)"
    after = r"(?:未提供|不提供|缺失|没有|不存在)"
    return bool(re.search(rf"{before}\s*{re.escape(value)}|{re.escape(value)}\s*{after}", text))


def metric_clause(text: str, name: str) -> str | None:
    fragments = re.split(r"[;；。,，\n]", text)
    # Chinese headings can contain layout spaces in PDF/table originals.
    # Match within one clause only; preserve the original text for values/quotes.
    label = re.sub(r"[ \u3000]+", "", name)
    chinese_label = re.fullmatch(r"[\u3400-\u9fff]+", label) is not None
    matches = [fragment for fragment in fragments if mentions(fragment, name)]
    if not matches and chinese_label:
        matches = [fragment for fragment in fragments
                   if label in re.sub(r"[ \u3000]+", "", fragment)]
    if len(matches) != 1:
        return None
    # Standalone tax qualifications can follow a price without moving a number.
    qualifications = [fragment for fragment in fragments if not re.search(r"\d", fragment)
                      and re.fullmatch(r"\s*(?:价格)?(?:均)?(?:不含税|未含税|含税|税前|税后)\s*", fragment)]
    return " ".join((matches[0], *qualifications))


def enum_choices(text: str, name: str) -> list[str]:
    match = re.search(re.escape(name), text, re.IGNORECASE)
    if match is None:
        return []
    tail = text[match.end():].strip()
    tail = re.sub(r"^(?:[:：=]\s*)?(?:必须为|须为|应为|可为|必须|采用|为|是)?\s*", "", tail)
    return [value.strip() for value in re.split(r"或者|或|、|[/／]", tail) if value.strip()]


def item_supported(text: str, item: ProcurementItem, *, model: str | None = None) -> bool:
    quoted_models = re.findall(r"型号\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9._-]{0,79})", text)
    if model and any(quoted.casefold() != model.casefold() for quoted in quoted_models):
        return False
    if item.specification and any(not mentions(item.specification, quoted) for quoted in quoted_models):
        return False
    if mentions(text, item.name):
        return True
    # An explicit model specification may bind a supplier paragraph to an item.
    return bool(model and mentions(item.specification, model))


def source_comparator(text: str) -> str:
    candidates = []
    for operator, pattern in (
        ("gte", r"至少|不低于|不少于|不小于|>=|≥|at least"),
        ("lte", r"至多|不超过|不高于|不大于|最多|以内|<=|≤|at most"),
        ("gt", r"(?<![<>=])>(?!=)|(?<!不)大于|(?<!不)超过|(?<!不)高于|(?<!不)多于"),
        ("lt", r"(?<![<>=])<(?!=)|(?<!不)小于|(?<!不)低于|(?<!不)少于"),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            candidates.append(operator)
    return candidates[0] if len(candidates) == 1 else "unknown" if candidates else "eq"


def strict_source_comparator(text: str, criterion: Criterion, item: ProcurementItem,
                             unit: str, *, model=None, start_event=None,
                             bound_aliases=False, allow_range=False, money_aliases_v13=False) -> str:
    """Accept finite affirmative measurements; unknown prose is never equality.

    Check the full verified source span, including qualifications outside the
    metric fragment. This deliberately sends unsupported phrasing to a human.
    Historical rules keep using source_comparator without this grammar.
    """
    if not unit:
        return "unknown"
    normalized_text = re.sub(r"\s+", "", text).casefold()
    # Labels are user-supplied text, not authority to reinterpret a known
    # negation or capability claim as a committed value. The affirmative
    # grammar below still rejects other, unrecognized wording.
    if COMPLEX.search(normalized_text) or re.search(
            r"不足|不到|不满|不提供|未达到|并非|可达|不能保证|不保证|未承诺|不承诺", normalized_text):
        return "unknown"
    suffix = r"(?![A-Za-z])" if unit[-1:].isascii() and unit[-1:].isalpha() else ""
    measure_word = r"(?:个\s*)?" if criterion.rule.type == "duration" and unit in {"自然日", "工作日", "月"} else ""
    quantity = NUMBER_PATTERN
    if allow_range:
        quantity += rf"(?:\s*(?:{re.escape(unit)})?\s*(?:至|到|[-~～])\s*{NUMBER_PATTERN})?"
    flags = 0 if unit.casefold() in {"mb", "gb", "tb", "mib", "gib", "tib"} else re.IGNORECASE
    measurements = list(re.finditer(rf"{quantity}\s*{measure_word}{re.escape(unit)}{suffix}", text, flags))
    if len(measurements) != 1:
        return "unknown"
    measurement = measurements[0]
    normalize = lambda value: re.sub(r"\s+", "", value).casefold()
    labels = [item.name, criterion.name]
    head = normalize(text[:measurement.start()])
    metric_start = head.find(normalize(criterion.name))
    if model and metric_start >= 0 and normalize(model) in head[:metric_start]:
        labels.extend((model, f"型号{model}"))
    label = criterion.name.removeprefix(item.name).strip()
    if bound_aliases and label in {"数量", "采购数量"} and criterion.rule.type == "numeric":
        labels.extend(("数量", "采购数量"))
    if bound_aliases and label in {"单价", "含税单价", "采购单价", "单价上限", "含税单价上限", "采购单价上限"} and criterion.rule.type == "money":
        labels.extend(("单价", "含税单价", "采购单价"))
    if bound_aliases and money_aliases_v13 and criterion.rule.type == "money" and label in {
            "单价", "采购单价", "含税单价", "不含税单价", "未含税单价", "税前单价",
            "单价上限", "采购单价上限", "含税单价上限", "不含税单价上限", "未含税单价上限", "税前单价上限"}:
        labels.extend(("不含税单价", "未含税单价", "税前单价", "含税单价", "采购单价", "单价"))
    tokens = [re.escape(normalize(value)) for value in labels if value]
    tokens.extend((r"[:：=()（）,，;；。]", "为", "是", "等于", "提供", "供货", "交付", "交货",
                   "承诺", "合同约定", "至少", "不低于", "不少于", "不小于", "至多", "不超过",
                   "不高于", "不大于", "最多", "大于", "超过", "高于", "多于", "小于", "低于", "少于",
                   r">=|<=|≥|≤|>|<", "atleast", "atmost"))
    tails = [r"[()（）,，;；。]", "供货", "交付", "交货", "提供", "完成", "完毕", "以内"]
    if criterion.rule.type == "money":
        basis = ["人民币", "cny", "rmb", "usd", "eur", "￥", "¥", "含税", "不含税", "未含税", "税前", "税后"]
        tokens.extend(basis)
        tails.extend(basis)
    if criterion.rule.type == "duration":
        tokens.extend(re.escape(normalize(value)) for value in (*DURATION_START_EVENTS, start_event) if value)
        tokens.extend(("之日起", "之后", "以后", "后", "起", "自"))
        tails.extend(("内", "前"))
    before, after = normalize(text[:measurement.start()]), normalize(text[measurement.end():])
    # Longest first, with a possessive repetition, prevents overlapping labels
    # in untrusted text from causing exponential regular-expression backtracking.
    prefix_pattern = r"(?:" + "|".join(sorted(set(tokens), key=len, reverse=True)) + r")*+"
    suffix_pattern = r"(?:" + "|".join(sorted(set(tails), key=len, reverse=True)) + r")*+"
    if not re.fullmatch(prefix_pattern, before) or not re.fullmatch(suffix_pattern, after):
        return "unknown"
    comparison_text = normalize(text).replace("atleast", "at least").replace("atmost", "at most")
    comparator = source_comparator(comparison_text)
    if criterion.rule.type == "duration" and re.match(r"(?:以内|内|前)", after):
        comparator = "lte" if comparator in {"eq", "lte"} else "unknown"
    return comparator


def number_pairs(text: str, unit: str, *, legacy_duration=True) -> list[Decimal]:
    if not unit:
        return []
    suffix = r"(?![A-Za-z])" if unit[-1:].isascii() and unit[-1:].isalpha() else ""
    measure_word = r"(?:个\s*)?" if not legacy_duration and unit in {"自然日", "工作日", "月"} else ""
    pattern = rf"({NUMBER_PATTERN})\s*{measure_word}{re.escape(unit)}{suffix}"
    flags = 0 if unit.casefold() in {"mb", "gb", "tb", "mib", "gib", "tib"} else re.IGNORECASE
    return [Decimal(match[1]) for match in re.finditer(pattern, text, flags)]


def money_basis(text: str, unit: str):
    currency_matches = set()
    for name, pattern in (("CNY", r"人民币|CNY|RMB|(?<!美)(?<!欧)元|￥|¥"),
                          ("USD", r"美元|USD|US\$"), ("EUR", r"欧元|EUR|€")):
        if re.search(pattern, text, re.IGNORECASE):
            currency_matches.add(name)
    currency = next(iter(currency_matches)) if len(currency_matches) == 1 else None
    per_matches = re.findall(r"(?:[/／]|每)\s*([^\s,，;；。.\d/／]{1,20})", unit)
    per_unit = per_matches[0] if len(per_matches) == 1 else None
    if per_unit is None:
        matches = re.findall(r"每\s*([^\s,，;；。.\d]{1,10})\s*(?:单价|价格|报价|售价|[0-9])", text)
        per_unit = matches[0] if len(set(matches)) == 1 else None
    excluded = bool(re.search(r"不含税|未含税|税前|excluded", text, re.IGNORECASE))
    included = bool(re.search(r"(?<!不)(?<!未)含税|税后|included", text, re.IGNORECASE))
    tax = "excluded" if excluded and not included else "included" if included and not excluded else "unspecified"
    return currency, per_unit, tax


_DURATION_UNITS = {
    "natural_days": ("自然日", "个自然日", "天", "日", "natural_day", "natural_days", "day", "days"),
    "working_days": ("工作日", "个工作日", "working_day", "working_days", "workday", "workdays"),
    "months": ("个月", "月", "month", "months"),
}

DURATION_START_EVENTS = ("合同签订", "订单确认", "收到预付款", "收到付款", "收到通知", "下单", "验收", "交付")


def canonical_start_event(value: str | None) -> str | None:
    # Only trailing temporal grammar is removed, never business events or
    # conjunctions. Distinct payment/contract/order conditions stay distinct.
    if value is None:
        return None
    return re.sub(r"(?:之日起|之后|以后|后|起)$", "", value.strip()).strip()


def duration_start_events(text: str, *, hints=(), legacy_duration=False, normalize_start=False) -> list[str]:
    if not legacy_duration:
        # Explicit metric labels do not describe a start event. Remove only
        # the label, so a separate delivery/start condition remains visible.
        text = re.sub(r"交\s*付\s*(?:期\s*限|时\s*间|周\s*期)", "", text)
    possible = [event for event in dict.fromkeys((*hints, *DURATION_START_EVENTS))
                if event and mentions(text, event)]
    if normalize_start:
        possible = list(dict.fromkeys(canonical_start_event(event) for event in possible))
    measurement = re.search(NUMBER_PATTERN, text)
    # A bare terminal delivery verb is an end point. Explicit conditions
    # elsewhere in the source remain visible, including after the number.
    if (not legacy_duration and measurement is not None
            and not mentions(text[:measurement.start()], "交付")
            and re.fullmatch(r".*(?:内|前)交付(?:完成|完毕)?\s*", text[measurement.start():])):
        possible = [event for event in possible if event != "交付"]
    return possible


def duration_unit_calendar(unit: str, *, legacy_duration=False) -> str | None:
    if legacy_duration and unit in {"个自然日", "个工作日"}:
        return None
    return next((calendar for calendar, units in _DURATION_UNITS.items()
                 if unit.casefold() in units), None)


def duration_measurements(text: str, *, legacy_duration=False) -> list[tuple[Decimal, str]]:
    units = [unit for group in _DURATION_UNITS.values() for unit in group]
    if legacy_duration:
        units = [unit for unit in units if unit not in {"个自然日", "个工作日"}]
    units.extend(("自然周", "周", "年", "小时", "分钟", "week", "weeks", "year", "years", "hour", "hours"))
    choices = "|".join(re.escape(unit) for unit in sorted(units, key=len, reverse=True))
    return [(Decimal(match[1]), match[2]) for match in re.finditer(
        rf"({NUMBER_PATTERN})\s*({choices})(?![A-Za-z])", text, re.IGNORECASE)]


def duration_calendar(text: str, unit: str, *, legacy_duration=False) -> str | None:
    calendar = duration_unit_calendar(unit, legacy_duration=legacy_duration)
    if calendar is None:
        return None
    calendars = {calendar}
    if re.search(r"工作日|working[_ ]?days?|workdays?", text, re.IGNORECASE):
        calendars.add("working_days")
    if re.search(r"自然日|natural[_ ]?days?", text, re.IGNORECASE):
        calendars.add("natural_days")
    return next(iter(calendars)) if len(calendars) == 1 else None


def validate_definition(definition: ChecklistDefinition | dict, repo: EvidenceRepository) -> None:
    definition = (definition if isinstance(definition, ChecklistDefinition)
                  else ChecklistDefinition.model_validate(definition))
    for criterion in definition.criteria:
        context = criterion.source.context
        if context is None:
            continue
        blocks, text = source_span(repo, context.evidence_refs, context.quote)
        if any(block.role != "requirement" or block.supplier is not None for block in blocks):
            raise ValueError("requirement_source_scope")
        if (criterion.source.kind == "document_extracted"
                and _structural_quote(context.quote) != _structural_quote(text)):
            raise ValueError("requirement_quote_incomplete")


def rule_supported(criterion: Criterion, item: ProcurementItem, *, legacy_duration=False,
                   normalize_start=False, strict_numeric=False) -> bool:
    """Prove a finite document rule; unresolved prose remains a candidate."""
    if criterion.requires_manual_review:
        return False
    if criterion.source.kind == "user_defined":
        return True
    original = criterion.source.context.quote
    text = metric_clause(original, criterion.name)
    rule = criterion.rule
    complexity_text = re.sub(r"或者|或", "", original) if rule.type == "enum" and rule.operator == "in" else original
    if (not text or COMPLEX.search(complexity_text) or not mentions(text, criterion.name)
            or not item_supported(text, item)):
        return False
    if rule.type == "presence":
        return (mentions(text, rule.description)
                and (not negated(text, rule.description)) == rule.must_be_present)
    if rule.type == "enum":
        choices = enum_choices(text, criterion.name)
        return (set(choices) == set(rule.values) and len(choices) == len(rule.values)
                and all(not negated(text, value) for value in rule.values))
    if rule.type == "manual":
        return False
    unit = rule.unit if rule.type == "numeric" else (
        {"natural_days": "自然日", "working_days": "工作日", "months": "月"}[rule.calendar]
        if rule.type == "duration" else "")
    if rule.type == "duration":
        if not mentions(text, rule.start_event):
            return False
        expected_start = canonical_start_event(rule.start_event) if normalize_start else rule.start_event
        if not legacy_duration and duration_start_events(original, hints=(rule.start_event,), normalize_start=normalize_start) != [expected_start]:
            return False
        if len(duration_measurements(text, legacy_duration=legacy_duration)) != 1 or len(re.findall(NUMBER_PATTERN, text)) != 1:
            return False
        unit = next((alias for alias in _DURATION_UNITS[rule.calendar]
                     if not (legacy_duration and alias in {"个自然日", "个工作日"})
                     and number_pairs(text, alias, legacy_duration=legacy_duration)), "")
        if duration_calendar(text, unit, legacy_duration=legacy_duration) != rule.calendar:
            return False
    if rule.type == "money":
        currencies = {"CNY": ("元", "人民币", "CNY", "RMB"),
                      "USD": ("美元", "USD"), "EUR": ("欧元", "EUR")}[rule.currency]
        possible = [f"{currency}/{rule.per_unit}" for currency in currencies]
        unit = next((entry for entry in possible if mentions(text, entry)), "")
        basis_text = re.sub(r"\s+", "", original) if strict_numeric else text
        if not unit or money_basis(basis_text, unit) != (rule.currency, rule.per_unit, rule.tax_basis):
            return False
    if rule.operator == "between":
        pattern = rf"({NUMBER_PATTERN})\s*(?:{re.escape(unit)})?\s*(?:至|到|[-~～])\s*({NUMBER_PATTERN})\s*{re.escape(unit)}"
        return (not strict_numeric or strict_source_comparator(original, criterion, item, unit, allow_range=True) == "eq") and any(Decimal(m[1]) == Decimal(rule.lower) and Decimal(m[2]) == Decimal(rule.upper)
                   for m in re.finditer(pattern, text, re.IGNORECASE))
    pairs = number_pairs(text, unit, legacy_duration=legacy_duration or rule.type != "duration")
    comparator = (strict_source_comparator(original, criterion, item, unit, start_event=rule.start_event if rule.type == "duration" else None)
                  if strict_numeric else source_comparator(text))
    return (len(pairs) == 1 and pairs[0] == Decimal(rule.value)
            and comparator == rule.operator)
