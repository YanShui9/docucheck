"""Finite rule contracts. These definitions do not execute expressions or model code."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Label = Annotated[
    str,
    Field(
        min_length=1,
        max_length=120,
        pattern=r"^[^\s\x00-\x1f](?:[^\x00-\x1f]*[^\s\x00-\x1f])?$",
    ),
]
Number = Annotated[
    str, Field(pattern=r"^-?[0-9]{1,12}(?:\.[0-9]{1,6})?$", max_length=20)
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Comparison(Contract):
    operator: Literal["eq", "gte", "lte", "gt", "lt", "between"]
    value: Number | None = None
    lower: Number | None = None
    upper: Number | None = None

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.operator == "between":
            if (
                self.value is not None
                or self.lower is None
                or self.upper is None
                or Decimal(self.lower) > Decimal(self.upper)
            ):
                raise ValueError("invalid_range")
        elif self.value is None or self.lower is not None or self.upper is not None:
            raise ValueError("invalid_threshold")
        return self


class NumericRule(Comparison):
    type: Literal["numeric"]
    unit: Label


class MoneyRule(Comparison):
    type: Literal["money"]
    currency: Literal["CNY", "USD", "EUR"]
    per_unit: Label
    tax_basis: Literal["included", "excluded", "unspecified"]

    @model_validator(mode="after")
    def nonnegative(self):
        if any(
            Decimal(n) < 0
            for n in (self.value, self.lower, self.upper)
            if n is not None
        ):
            raise ValueError("negative_money")
        return self


class EnumRule(Contract):
    type: Literal["enum"]
    operator: Literal["eq", "in"]
    values: list[Label] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_values(self):
        if len(set(self.values)) != len(self.values) or (
            self.operator == "eq" and len(self.values) != 1
        ):
            raise ValueError("invalid_enum")
        return self


class PresenceRule(Contract):
    type: Literal["presence"]
    description: Label
    must_be_present: bool = True


class DurationRule(Comparison):
    type: Literal["duration"]
    calendar: Literal["natural_days", "working_days", "months"]
    start_event: Label

    @model_validator(mode="after")
    def nonnegative(self):
        if any(
            Decimal(n) < 0
            for n in (self.value, self.lower, self.upper)
            if n is not None
        ):
            raise ValueError("negative_duration")
        return self


class ManualRule(Contract):
    type: Literal["manual"]
    instruction: str = Field(min_length=1, max_length=1000, pattern=r"\S")


Rule = Annotated[
    NumericRule | MoneyRule | EnumRule | PresenceRule | DurationRule | ManualRule,
    Field(discriminator="type"),
]


class EvidenceContext(Contract):
    evidence_refs: list[str] = Field(min_length=1, max_length=4)
    quote: str = Field(min_length=1, max_length=2000, pattern=r"\S")

    @model_validator(mode="after")
    def unique_refs(self):
        if any(not 1 <= len(ref) <= 64 for ref in self.evidence_refs) or len(
            set(self.evidence_refs)
        ) != len(self.evidence_refs):
            raise ValueError("invalid_context_refs")
        return self


class UserDefinedSource(Contract):
    kind: Literal["user_defined"]
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    context: EvidenceContext | None = None


class DocumentSource(Contract):
    kind: Literal["document_extracted"]
    context: EvidenceContext


class ProcurementItem(Contract):
    item_id: Identifier
    name: Label
    specification: str = Field(default="", max_length=500)


class Criterion(Contract):
    criterion_id: Identifier
    item_id: Identifier
    name: Label
    rule: Rule
    source: Annotated[UserDefinedSource | DocumentSource, Field(discriminator="kind")]
    required: bool = True
    enabled: bool = True
    conditions: list[Label] = Field(default_factory=list, max_length=8)

    @property
    def requires_manual_review(self):
        return self.rule.type == "manual" or bool(self.conditions)


class ChecklistDefinition(Contract):
    items: list[ProcurementItem] = Field(min_length=1, max_length=20)
    criteria: list[Criterion] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def scope_and_identity(self):
        ids = {item.item_id for item in self.items}
        if len(ids) != len(self.items) or len(
            {c.criterion_id for c in self.criteria}
        ) != len(self.criteria):
            raise ValueError("duplicate_identity")
        if any(c.item_id not in ids for c in self.criteria):
            raise ValueError("unknown_item")
        return self

    def issues(self):
        from .validation import rule_supported

        result, names = [], set()
        items = {item.item_id: item for item in self.items}
        for criterion in self.criteria:
            if not criterion.enabled:
                continue
            identity = (criterion.item_id, criterion.name)
            if identity in names:
                result.append(
                    {"criterion_id": criterion.criterion_id, "code": "duplicate_name"}
                )
            names.add(identity)
            if criterion.source.kind == "document_extracted" and not rule_supported(
                criterion, items[criterion.item_id]
            ):
                result.append({"criterion_id": criterion.criterion_id, "code": "rule_source_unresolved"})
            if (
                criterion.rule.type == "money"
                and criterion.rule.tax_basis == "unspecified"
            ):
                result.append(
                    {
                        "criterion_id": criterion.criterion_id,
                        "code": "tax_basis_missing",
                    }
                )
        return result

    def selected(self, ids: list[str]):
        selected = [c for c in self.criteria if c.criterion_id in ids]
        item_ids = {c.item_id for c in selected}
        return ChecklistDefinition(
            items=[i for i in self.items if i.item_id in item_ids], criteria=selected
        )
