"""Small typed interfaces shared by corpus authoring, adapters and graders."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Nonempty = Annotated[str, Field(min_length=1, max_length=16000)]
Category = Literal["intent", "rag", "review", "memory", "safety"]
Split = Literal["development", "measurement"]
StateKey = Literal["case_id", "checklist_version", "bindings_version", "snapshot_version",
                   "review_count", "confirmed", "provider_dispatch_count", "export_count"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


def safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute()
            or any(p in (".", "..", "") for p in value.split("/"))):
        raise ValueError("unsafe_relative_path")
    return value


class Turn(Contract):
    role: Literal["user", "assistant"]
    content: Nonempty


class Source(Contract):
    source_id: Identifier
    group_id: Identifier
    supplier_id: Identifier | None
    role: Literal["quote", "spec", "requirement"]
    path: str
    format: Literal["pdf", "xlsx"]
    pages: list[list[Nonempty]] = Field(default_factory=list)
    sheets: dict[str, list[list[str]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def content_and_path(self):
        if (self.role == "requirement") != (self.supplier_id is None):
            raise ValueError("source_role_supplier_mismatch")
        safe_relative(self.path)
        if not self.path.startswith(f"inputs/{self.group_id}/"):
            raise ValueError("source_path_group_mismatch")
        if not self.path.endswith("." + self.format):
            raise ValueError("source_extension_mismatch")
        if self.format == "pdf":
            if not self.pages or self.sheets or any(not page for page in self.pages):
                raise ValueError("invalid_pdf_pages")
        elif self.pages or not self.sheets:
            raise ValueError("invalid_xlsx_sheets")
        if self.format == "xlsx" and any(
                len(cell) > 2000 for rows in self.sheets.values() for row in rows for cell in row):
            raise ValueError("xlsx_cell_exceeds_supported_contract")
        return self


class Anchor(Contract):
    anchor_id: Identifier
    source_id: Identifier
    text: Nonempty
    page: int | None = Field(default=None, ge=1)
    line: int | None = Field(default=None, ge=1)
    sheet: str | None = None
    cell: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,5}$")

    @model_validator(mode="after")
    def location(self):
        pdf = self.page is not None and self.line is not None
        xlsx = self.sheet is not None and self.cell is not None
        if pdf == xlsx or (pdf and (self.sheet is not None or self.cell is not None)):
            raise ValueError("exactly_one_anchor_location_required")
        if xlsx and (self.page is not None or self.line is not None):
            raise ValueError("exactly_one_anchor_location_required")
        return self


class FactClaim(Contract):
    claim_id: Identifier
    supplier_id: Identifier
    item_id: Identifier
    criterion_id: Identifier
    kind: Literal["numeric", "money", "enum", "presence", "duration", "text"]
    value: str | bool
    unit: str | None = None
    negated: bool = False
    tax_basis: Literal["included", "excluded", "unspecified", "conflicting"] | None = None
    currency: str | None = None
    anchor_ids: list[Identifier] = Field(min_length=1)


class StateAssertion(Contract):
    key: StateKey
    expected: str | int | bool


class ReviewExpectation(Contract):
    supplier_id: Identifier
    item_id: Identifier
    criterion_id: Identifier
    business_status: Literal["satisfied", "not_satisfied", "conflict",
                             "insufficient_evidence", "manual"] | None
    processing_status: Literal["complete", "partial"]
    is_formal: bool
    reference_answer: Nonempty
    necessary_anchor_groups: list[list[Identifier]] = Field(min_length=1)


class ExpectedOutcome(Contract):
    kind: Literal["clarification", "answer", "review", "history", "refusal", "draft",
                  "export", "retry", "read_only"]
    outcome: Literal["clarify", "answered", "unanswerable", "draft_only", "review_requested",
                     "retry_requested", "export_requested", "satisfied", "not_satisfied",
                     "manual", "partial", "history_found", "rejected", "no_mutation"]
    reference_answer: Nonempty
    answerable: bool | None = None
    fact_claims: list[FactClaim] = Field(default_factory=list)
    # Every outer group is necessary; at least one anchor in each inner group suffices.
    necessary_anchor_groups: list[list[Identifier]] = Field(default_factory=list)
    required_behaviors: list[Identifier] = Field(min_length=1)
    forbidden_behaviors: list[Identifier] = Field(min_length=1)
    immutable_state_assertions: list[StateAssertion] = Field(min_length=1)
    # Alternatives, never an exact full tool sequence. Empty means state/output suffices.
    allowed_tool_paths: list[list[str]] = Field(default_factory=list)
    business_checks: list[ReviewExpectation] = Field(default_factory=list)
    extra_result_policy: Literal["reject_unexpected_checks"] = "reject_unexpected_checks"

    @model_validator(mode="after")
    def conservative_review(self):
        if any(not group for group in self.necessary_anchor_groups):
            raise ValueError("empty_anchor_group")
        if (self.kind == "review" and any(c.kind == "presence" and c.value is True
                                         for c in self.fact_claims)
                and self.outcome != "manual"):
            raise ValueError("presence_true_requires_manual")
        if "coverage_incomplete" in self.required_behaviors and self.outcome != "partial":
            raise ValueError("incomplete_coverage_not_formal")
        if ((self.outcome == "partial" or any(c.processing_status == "partial" for c in self.business_checks))
                and any(c.is_formal for c in self.business_checks)):
            raise ValueError("partial_run_not_formal")
        for check in self.business_checks:
            if check.processing_status == "partial" and check.is_formal:
                raise ValueError("partial_check_not_formal")
            if any(not group for group in check.necessary_anchor_groups):
                raise ValueError("empty_business_anchor_group")
        return self


class Provenance(Contract):
    kind: Literal["old_failure", "product_need"]
    reference: Nonempty
    rationale: Nonempty


class Task(Contract):
    task_id: Identifier
    group_id: Identifier
    category: Category
    split: Split
    prompt: Nonempty
    turns: list[Turn] = Field(default_factory=list)
    setup_id: Identifier
    expected: ExpectedOutcome
    provenance: Provenance
    pair_tags: list[Identifier] = Field(min_length=1)
    repeat_selected: bool = False


class Supplier(Contract):
    supplier_id: Identifier
    name: Nonempty


class Item(Contract):
    item_id: Identifier
    name: Nonempty
    sku: Nonempty


class Binding(Contract):
    supplier_id: Identifier
    item_id: Identifier
    source_id: Identifier
    pages: list[int] = Field(default_factory=list)
    sheet: str | None = None
    cells: list[str] = Field(default_factory=list)
    coverage: Literal["complete", "incomplete"] = "complete"
    public_identity_text: Nonempty


class FixturePrecondition(Contract):
    kind: Literal["existing_unparsed_bound_page", "existing_failed_review",
                  "existing_unknown_dispatch", "history_replay_with_runtime_ids",
                  "viewer_identity", "existing_partial_report"]
    requirement: Nonempty
    on_unavailable: Literal["setup_blocked"]


class Setup(Contract):
    setup_id: Identifier
    group_id: Identifier
    case_title: Nonempty
    source_ids: list[Identifier] = Field(min_length=1)
    suppliers: list[Supplier] = Field(min_length=2)
    items: list[Item] = Field(min_length=1)
    # Each entry is a product criterion: id, item_id, name, rule (ChecklistDefinition).
    confirmed_rules: list[dict] = Field(default_factory=list)
    bindings: list[Binding] = Field(min_length=1)
    history: list[dict] = Field(default_factory=list)
    state: dict[StateKey, str | int | bool]
    context: dict = Field(default_factory=dict)
    preconditions: list[FixturePrecondition] = Field(default_factory=list)

    @model_validator(mode="after")
    def authority_shape(self):
        if self.state.get("confirmed") is False and self.confirmed_rules:
            raise ValueError("unconfirmed_setup_cannot_have_confirmed_rules")
        if not self.items or len({i.item_id for i in self.items}) != len(self.items):
            raise ValueError("invalid_setup_item_identity")
        if len({s.supplier_id for s in self.suppliers}) != len(self.suppliers):
            raise ValueError("duplicate_setup_supplier")
        for criterion in self.confirmed_rules:
            if (set(criterion) != {"criterion_id", "item_id", "name", "rule", "source"}
                    or criterion["item_id"] not in {i.item_id for i in self.items}
                    or criterion["source"].get("kind") != "user_defined"):
                raise ValueError("invalid_prepared_criterion")
        if (self.state.get("confirmed") is True
                and {i.item_id for i in self.items}
                != {c["item_id"] for c in self.confirmed_rules}):
            raise ValueError("confirmed_setup_has_unreferenced_items")
        return self


class Dataset(Contract):
    schema_version: Literal[1]
    revision: Literal[1, 2, 3, 4, 5, 6] = 1
    dataset_id: Literal["docucheck-agent-interview-v1"]
    synthetic: Literal[True]
    author_visible: Literal[True]
    independent_holdout: Literal[False]
    license: Literal["CC0-1.0"]
    tasks: list[Task] = Field(min_length=40, max_length=40)
    sources: list[Source]
    anchors: list[Anchor]
    setups: list[Setup]
    repeat_task_ids: list[Identifier] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def frozen_protocol(self):
        def keyed(values, attribute):
            result = {getattr(v, attribute): v for v in values}
            if len(result) != len(values):
                raise ValueError("duplicate_" + attribute)
            return result

        tasks = keyed(self.tasks, "task_id")
        sources = keyed(self.sources, "source_id")
        anchors = keyed(self.anchors, "anchor_id")
        setups = keyed(self.setups, "setup_id")
        if len({s.path for s in self.sources}) != len(self.sources):
            raise ValueError("duplicate_source_path")
        groups = {}
        counts = {"development": [2, 3, 3, 1, 1], "measurement": [6, 9, 7, 3, 5]}
        for split, expected in counts.items():
            actual = Counter(t.category for t in self.tasks if t.split == split)
            if [actual[c] for c in ("intent", "rag", "review", "memory", "safety")] != expected:
                raise ValueError("frozen_category_split_counts")
        for task in self.tasks:
            if task.group_id in groups and groups[task.group_id] != task.split:
                raise ValueError("procurement_group_split_leakage")
            groups[task.group_id] = task.split
            setup = setups.get(task.setup_id)
            if setup is None or setup.group_id != task.group_id:
                raise ValueError("task_setup_scope_mismatch")
            for group in task.expected.necessary_anchor_groups:
                for key in group:
                    if key not in anchors or anchors[key].source_id not in setup.source_ids:
                        raise ValueError("task_anchor_scope_mismatch")
            for claim in task.expected.fact_claims:
                if (claim.item_id not in {i.item_id for i in setup.items}
                        or claim.supplier_id not in {s.supplier_id for s in setup.suppliers}):
                    raise ValueError("claim_identity_scope_mismatch")
                for key in claim.anchor_ids:
                    if key not in anchors or anchors[key].source_id not in setup.source_ids:
                        raise ValueError("claim_anchor_scope_mismatch")
            if task.category == "review":
                required_checks = {(s.supplier_id, c["item_id"], c["criterion_id"])
                                   for s in setup.suppliers for c in setup.confirmed_rules}
                actual_checks = {(c.supplier_id, c.item_id, c.criterion_id)
                                 for c in task.expected.business_checks}
                if (actual_checks != required_checks
                        or len(actual_checks) != len(task.expected.business_checks)):
                    raise ValueError("incomplete_review_result_matrix")
                for check in task.expected.business_checks:
                    for group in check.necessary_anchor_groups:
                        if group not in task.expected.necessary_anchor_groups:
                            raise ValueError("business_evidence_not_required_at_task_level")
                        for key in group:
                            if (key not in anchors
                                    or anchors[key].source_id not in setup.source_ids
                                    or sources[anchors[key].source_id].supplier_id != check.supplier_id):
                                raise ValueError("business_anchor_scope_mismatch")
        for setup in self.setups:
            document_slots = set()
            for key in setup.source_ids:
                if key not in sources or sources[key].group_id != setup.group_id:
                    raise ValueError("setup_source_scope_mismatch")
                # Product uploads replace the same supplier/role slot across formats.
                # Requirements use the single case-wide (None, requirement) slot.
                slot = (sources[key].supplier_id, sources[key].role)
                if slot in document_slots:
                    raise ValueError("duplicate_setup_document_slot")
                document_slots.add(slot)
            for binding in setup.bindings:
                if (binding.source_id not in setup.source_ids
                        or binding.item_id not in {i.item_id for i in setup.items}
                        or binding.supplier_id not in {s.supplier_id for s in setup.suppliers}):
                    raise ValueError("binding_scope_mismatch")
                source = sources[binding.source_id]
                if source.supplier_id != binding.supplier_id:
                    raise ValueError("binding_supplier_mismatch")
                if source.format == "pdf":
                    if (not binding.pages or binding.sheet is not None or binding.cells
                            or any(type(p) is not int or p < 1 or p > len(source.pages)
                                   for p in binding.pages)
                            or binding.pages != list(range(binding.pages[0], binding.pages[-1] + 1))):
                        raise ValueError("invalid_binding_page_range")
                else:
                    if binding.pages or binding.sheet not in source.sheets or not binding.cells:
                        raise ValueError("invalid_binding_sheet_range")
                    from openpyxl.utils.cell import range_boundaries
                    rows = source.sheets[binding.sheet]
                    for cells in binding.cells:
                        try:
                            low_col, low_row, high_col, high_row = range_boundaries(cells)
                        except ValueError as exc:
                            raise ValueError("invalid_binding_cell_range") from exc
                        if (any(v is None for v in (low_col, low_row, high_col, high_row))
                                or min(low_col, low_row) < 1 or high_row > len(rows)
                                or high_col > min(len(row) for row in rows[low_row - 1:high_row])
                                or high_col < low_col or high_row < low_row):
                            raise ValueError("invalid_binding_cell_range")
        for anchor in self.anchors:
            source = sources.get(anchor.source_id)
            if source is None:
                raise ValueError("unknown_anchor_source")
            if source.format == "pdf":
                if (anchor.page is None or anchor.line is None
                        or anchor.page > len(source.pages)
                        or anchor.line > len(source.pages[anchor.page - 1])
                        or anchor.text != source.pages[anchor.page - 1][anchor.line - 1]):
                    raise ValueError("anchor_pdf_location_mismatch")
            else:
                if anchor.sheet not in source.sheets or anchor.cell is None:
                    raise ValueError("anchor_xlsx_location_mismatch")
                from openpyxl.utils.cell import coordinate_to_tuple
                row, column = coordinate_to_tuple(anchor.cell)
                rows = source.sheets[anchor.sheet]
                if (row > len(rows) or column > len(rows[row - 1])
                        or anchor.text != rows[row - 1][column - 1]):
                    raise ValueError("anchor_xlsx_text_mismatch")
        for split, expected in (("development", (2, 1)), ("measurement", (6, 3))):
            rag = [t for t in self.tasks if t.category == "rag" and t.split == split]
            if (sum(t.expected.answerable is True for t in rag),
                    sum(t.expected.answerable is False for t in rag)) != expected:
                raise ValueError("frozen_rag_answerability_counts")
        repeats = set(self.repeat_task_ids)
        if (len(repeats) != 6 or not repeats <= tasks.keys()
                or any(tasks[t].split != "measurement" for t in repeats)
                or {tasks[t].category for t in repeats} != {
                    "intent", "rag", "review", "memory", "safety"}
                or any(t.repeat_selected != (t.task_id in repeats) for t in self.tasks)):
            raise ValueError("invalid_preselected_repeats")
        if len({t.prompt for t in self.tasks}) != 40:
            raise ValueError("duplicate_task_prompt")
        return self


class FileRecord(Contract):
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size: int = Field(ge=1)
    role: Literal["input", "gold"]

    @model_validator(mode="after")
    def path_role(self):
        safe_relative(self.path)
        if not self.path.startswith("inputs/" if self.role == "input" else "gold/"):
            raise ValueError("file_role_path_mismatch")
        return self


class CorpusManifest(Contract):
    schema_version: Literal[1]
    revision: Literal[1, 2, 3, 4, 5, 6] = 1
    dataset_id: Literal["docucheck-agent-interview-v1"]
    dataset_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    files: list[FileRecord]
    task_ids: list[Identifier] = Field(min_length=40, max_length=40)
    synthetic: Literal[True] = True
    author_visible: Literal[True] = True
    independent_holdout: Literal[False] = False

    @model_validator(mode="after")
    def unique_files(self):
        if len({f.path for f in self.files}) != len(self.files):
            raise ValueError("duplicate_manifest_path")
        if len(set(self.task_ids)) != 40:
            raise ValueError("duplicate_manifest_task")
        return self
