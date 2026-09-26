"""Whitelisted tools: model arguments cannot set server scope or mutate inputs."""
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..evidence.repository import EvidenceRepository
from ..evidence.retrieval import Retriever
from ..extraction.memory import FactSet, extract_memory
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


class Evaluate(Supplier):
    fact_set_id: str


class AnswerDraft(Empty):
    kind: Literal["check", "evidence"]
    check_result_ids: list[str] = Field(default_factory=list, max_length=1)
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
    "extract_facts": (Supplier, "Extract ALL memory facts across requirement and supplier documents; records coverage."),
    "evaluate_checks": (Evaluate, "Evaluate memory with deterministic rules on the returned fact_set_id."),
    "get_check_results": (Supplier, "Read cached verified memory result in this snapshot for follow-up questions."),
    "submit_answer": (AnswerDraft, "Use one verified check result. Extra evidence IDs must belong to it."),
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
        self.fact_sets: dict[str, FactSet] = {}
        self.results: dict[str, CheckResult] = {}
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
            facts = next((f for f in self.fact_sets.values() if f.supplier == args.supplier and f.complete), None)
            if facts is None:
                facts = extract_memory(self.repo, args.supplier, model_call)
                self.fact_sets[facts.fact_set_id] = facts
            data = {"fact_set_id": facts.fact_set_id, "supplier": facts.supplier,
                    "complete": facts.complete, "scanned_count": len(facts.scanned_ids),
                    "facts": [asdict(f) | {"value": str(f.value)} for f in facts.facts]}
            refs = [ref for f in facts.facts for ref in f.evidence_refs]
        elif name == "evaluate_checks":
            self.repo.assert_supplier(args.supplier)
            facts = self.fact_sets.get(args.fact_set_id)
            if facts is None or facts.supplier != args.supplier:
                raise ValueError("fact_set_scope")
            result = evaluate_memory(self.repo, args.supplier, list(facts.facts), scan_complete=facts.complete)
            self.results[result.check_result_id] = result
            data, refs = asdict(result), list(result.evidence_refs)
        elif name == "get_check_results":
            self.repo.assert_supplier(args.supplier)
            result = next((self.results[key] for key in reversed(self.results)
                           if self.results[key].supplier == args.supplier), None)
            data = asdict(result) if result else {}
            refs = list(result.evidence_refs) if result else []
        else:
            raise ValueError("control_action_not_tool")
        self.seen.update(refs)
        return {"status": "ok", "data": data, "evidence_refs": refs,
                "coverage_ref": self.repo.snapshot_id, "error": None}
