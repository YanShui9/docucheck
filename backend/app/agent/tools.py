"""Whitelisted tools: model arguments cannot set server scope or mutate inputs."""
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..evidence.repository import EvidenceRepository
from ..evidence.retrieval import Retriever
from ..extraction.fields import extract_fields
from ..extraction.memory import FactSet, extract_memory
from ..field_contract import FieldCheckResult, FieldFactSet, FieldKey
from ..rules.fields import evaluate_field
from ..rules.memory import CheckResult, evaluate_memory


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Search(Empty):
    query: str = Field(min_length=1, max_length=300)
    supplier: str | None = None
    limit: int = Field(default=6, ge=1, le=12)


class Read(Empty):
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    neighbors: int = Field(default=0, ge=0, le=1)
    start_char: int = Field(
        default=0, ge=0, description="Character offset for continuing one evidence ID."
    )


class Supplier(Empty):
    supplier: str = Field(min_length=1, max_length=100)


class Extract(Supplier):
    field_keys: list[FieldKey] = Field(default_factory=lambda: [FieldKey.MEMORY],
                                      min_length=1, max_length=8)
    scope: Literal["full"] = "full"


class Evaluate(Supplier):
    fact_set_id: str | None = None
    fact_set_ids: list[str] = Field(default_factory=list, max_length=8)


class Results(Supplier):
    field_keys: list[FieldKey] | None = Field(default=None, max_length=8)


class AnswerDraft(Empty):
    kind: Literal["check", "evidence"]
    check_result_ids: list[str] = Field(default_factory=list, max_length=24)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class Clarification(Empty):
    question: str = Field(min_length=1, max_length=500)


DEFINITIONS = {
    "list_case_sources": (Empty, "List the server-bound documents, supplier handles and coverage."),
    "search_evidence": (Search, "Find relevant original evidence. No hit does not prove absence."),
    "read_source": (
        Read,
        ("Read original evidence and location. To continue, read one ID with "
         "start_char=next_char and neighbors=0."),
    ),
    "extract_facts": (Extract, "Fully scan all applicable documents for requested field_keys; records field coverage."),
    "evaluate_checks": (Evaluate, "Evaluate one or more fact set IDs using deterministic field rules."),
    "get_check_results": (Results, "Read cached verified results in this snapshot for follow-up questions."),
    "submit_answer": (AnswerDraft, "Use verified check results. Extra evidence IDs must belong to them."),
    "request_clarification": (Clarification, "Ask for missing user information or explain supported scope."),
}


def tool_schemas() -> list[dict]:
    return [{"type": "function", "function": {"name": name, "description": description,
             "parameters": schema.model_json_schema()}}
            for name, (schema, description) in DEFINITIONS.items()]


class ToolExecutor:
    def __init__(self, repo: EvidenceRepository):
        self.repo = repo
        self.retriever = Retriever(repo)
        self.fact_sets: dict[str, FactSet | FieldFactSet] = {}
        self.results: dict[str, CheckResult | FieldCheckResult] = {}
        self.seen: set[str] = set()

    def execute(self, name: str, arguments: dict, model_call) -> dict:
        if name not in DEFINITIONS:
            raise ValueError("unknown_tool")
        args = DEFINITIONS[name][0].model_validate(arguments)
        refs: list[str] = []
        if name == "list_case_sources":
            data = self.repo.sources()
        elif name == "search_evidence":
            data = self.retriever.search(args.query, supplier=args.supplier, limit=args.limit)
            refs = [ref for hit in data for ref in hit["evidence_refs"]]
        elif name == "read_source":
            data = self.retriever.read_source(
                args.evidence_ids, neighbors=args.neighbors, start_char=args.start_char
            )
            refs = [item["evidence_id"] for item in data]
        elif name == "extract_facts":
            self.repo.assert_supplier(args.supplier)
            if args.field_keys == [FieldKey.MEMORY]:
                facts = next((f for f in self.fact_sets.values()
                              if isinstance(f, FactSet) and f.supplier == args.supplier
                              and f.complete), None)
                if facts is None:
                    facts = extract_memory(self.repo, args.supplier, model_call)
                    self.fact_sets[facts.fact_set_id] = facts
                data = {"fact_set_id": facts.fact_set_id, "supplier": facts.supplier,
                        "complete": facts.complete, "scanned_count": len(facts.scanned_ids),
                        "facts": [asdict(f) | {"value": str(f.value)} for f in facts.facts]}
                refs = [ref for f in facts.facts for ref in f.evidence_refs]
            else:
                requested = tuple(dict.fromkeys(args.field_keys))
                cached = {field: next((f for f in self.fact_sets.values()
                                       if isinstance(f, FieldFactSet)
                                       and f.supplier == args.supplier
                                       and f.snapshot_id == self.repo.snapshot_id
                                       and f.field_key == field and f.complete), None)
                          for field in requested}
                if any(value is None for value in cached.values()):
                    extracted = extract_fields(self.repo, args.supplier, requested, model_call)
                    for fact_set in extracted.values():
                        self.fact_sets[fact_set.fact_set_id] = fact_set
                    cached = extracted
                selected = [cached[field] for field in requested]
                data = {"supplier": args.supplier, "fact_sets": [{
                    "fact_set_id": item.fact_set_id, "field_key": item.field_key.value,
                    "complete": item.complete, "scanned_count": len(item.scanned_ids),
                    "coverage_issues": item.coverage_issues,
                    "facts": [asdict(fact) | {"value": str(fact.value)}
                              for fact in item.facts],
                } for item in selected]}
                refs = [ref for item in selected for fact in item.facts
                        for ref in fact.evidence_refs]
        elif name == "evaluate_checks":
            self.repo.assert_supplier(args.supplier)
            fact_ids = args.fact_set_ids or ([args.fact_set_id] if args.fact_set_id else [])
            if not fact_ids or len(set(fact_ids)) != len(fact_ids):
                raise ValueError("fact_set_scope")
            selected = [self.fact_sets.get(key) for key in fact_ids]
            if any(item is None or item.supplier != args.supplier for item in selected):
                raise ValueError("fact_set_scope")
            if len(selected) == 1 and isinstance(selected[0], FactSet):
                facts = selected[0]
                result = evaluate_memory(self.repo, args.supplier, list(facts.facts),
                                         scan_complete=facts.complete)
                self.results[result.check_result_id] = result
                data, refs = asdict(result), list(result.evidence_refs)
            else:
                if any(not isinstance(item, FieldFactSet)
                       or item.snapshot_id != self.repo.snapshot_id for item in selected):
                    raise ValueError("fact_set_scope")
                if len({item.field_key for item in selected}) != len(selected):
                    raise ValueError("fact_set_scope")
                checks = [evaluate_field(self.repo, args.supplier, item.field_key,
                                         item.facts, scan_complete=item.complete)
                          for item in selected]
                for check in checks:
                    self.results[check.check_result_id] = check
                data = {"supplier": args.supplier,
                        "check_results": [asdict(check) for check in checks],
                        "check_result_ids": [check.check_result_id for check in checks]}
                refs = [ref for check in checks for ref in check.evidence_refs]
        elif name == "get_check_results":
            self.repo.assert_supplier(args.supplier)
            if args.field_keys is None:
                latest = {}
                for result in reversed(list(self.results.values())):
                    if result.supplier == args.supplier:
                        latest.setdefault(result.field_key, result)
                selected = list(reversed(list(latest.values())))
            else:
                selected = [next((self.results[key] for key in reversed(self.results)
                                  if self.results[key].supplier == args.supplier
                                  and self.results[key].field_key == field), None)
                            for field in dict.fromkeys(args.field_keys)]
            checks = [result for result in selected if result is not None]
            if args.field_keys is None and len(checks) == 1:
                data = asdict(checks[0])
                refs = list(checks[0].evidence_refs)
            else:
                data = {"supplier": args.supplier,
                        "check_results": [asdict(result) for result in checks],
                        "check_result_ids": [result.check_result_id for result in checks]}
                refs = [ref for result in checks for ref in result.evidence_refs]
        else:
            raise ValueError("control_action_not_tool")
        self.seen.update(refs)
        return {"status": "ok", "data": data, "evidence_refs": refs,
                "coverage_ref": self.repo.snapshot_id, "error": None}
