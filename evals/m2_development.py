"""Measure lexical retrieval and existing safety boundaries on synthetic data."""

import argparse
import json
from pathlib import Path

from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.evidence.retrieval import Retriever
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.app.rules.memory import evaluate_memory

DEFAULT_DATASET = Path(__file__).resolve().parent / "cases" / "lexical-001.json"


def score_recall_at_12(expected: set[str], hits: list[dict]) -> dict[str, int]:
    """Count each required source once, even when multiple chunks cite it."""
    returned = {ref for hit in hits[:12] for ref in hit["evidence_refs"]}
    return {"numerator": len(expected & returned), "denominator": len(expected)}


def _repository(source: dict) -> tuple[EvidenceRepository, dict[str, str]]:
    documents, locators = [], []
    for item in source["documents"]:
        document_id = item["document_id"]
        blocks = []
        for block in item["blocks"]:
            locator = f"{document_id}#{block['locator']}"
            if locator in locators:
                raise ValueError("duplicate_source_location")
            locators.append(locator)
            blocks.append(EvidenceBlock(
                evidence_id="", document_id=document_id, text=block["text"],
                page_idx=block.get("page_idx"), sheet=block.get("sheet"), cell=block.get("cell"),
            ))
        is_pdf = document_id.endswith(".pdf")
        status = item["status"]
        parsed = ParseResult(
            document_id=document_id, parser_version="eval-synthetic-v1", status=status,
            blocks=tuple(blocks), total_pages=1 if is_pdf else None,
            processed_pages=(0,) if is_pdf else (), total_sheets=None if is_pdf else 1,
            processed_sheets=() if is_pdf else ("报价",),
            coverage_issues=("synthetic_unverified_area",) if status == "partial" else (),
        )
        documents.append(Document(
            document_id=document_id,
            source_hash=digest([document_id, [
                (block.text, block.page_idx, block.sheet, block.cell) for block in blocks
            ]]),
            role=item["role"], supplier=item.get("supplier"), model=item.get("model"),
            parsed=parsed,
        ))
    repo = EvidenceRepository(source["case_id"], source["revision"], documents)
    location_ids = dict(zip(locators, (block.evidence_id for block in repo.blocks), strict=True))
    return repo, location_ids


def _guardrail_score(passed: bool) -> dict[str, int]:
    return {"numerator": int(passed), "denominator": 1}


def evaluate_dataset(path: Path = DEFAULT_DATASET) -> dict:
    """Use fixture documents as inputs; use gold locations only after retrieval."""
    source = json.loads(path.read_text(encoding="utf-8"))
    if source["dataset_kind"] != "synthetic_development":
        raise ValueError("development_dataset_required")
    repo, location_ids = _repository(source)
    retriever = Retriever(repo)
    by_evidence_id = {block.evidence_id: block for block in repo.blocks}
    queries = []
    total_found = total_relevant = scoped_queries = 0
    for query in source["queries"]:
        if any(location not in location_ids for location in query["relevant"]):
            raise ValueError("unknown_gold_location")
        if len(set(query["relevant"])) != len(query["relevant"]):
            raise ValueError("duplicate_gold_location")
        expected_ids = {location_ids[location] for location in query["relevant"]}
        hits = retriever.search(query["query"], supplier=query["supplier"], limit=12)
        found_ids = {ref for hit in hits for ref in hit["evidence_refs"]}
        score = score_recall_at_12(expected_ids, hits)
        total_found += score["numerator"]
        total_relevant += score["denominator"]
        foreign_refs = sorted(ref for ref in found_ids if by_evidence_id[ref].supplier
                              not in (None, query["supplier"]))
        scoped_queries += not foreign_refs
        queries.append({
            "id": query["id"], "supplier": query["supplier"],
            "numerator": score["numerator"], "denominator": score["denominator"],
            "missed_locations": [location for location in query["relevant"]
                                 if location_ids[location] not in found_ids],
            "hit_count": len(hits), "foreign_reference_count": len(foreign_refs),
        })

    probes = source["guardrail_probes"]
    try:
        repo.read(["eval-forged-evidence-id"])
        invalid_reference_rejected = False
    except ValueError as exc:
        invalid_reference_rejected = str(exc) == "evidence_scope"
    partial_supplier = probes["partial_supplier"]
    incomplete = evaluate_memory(repo, partial_supplier, [], scan_complete=True)
    partial_coverage_blocks_status = (
        not repo.coverage(partial_supplier)["complete"] and incomplete.business_status is None
    )
    complete_supplier = probes["complete_supplier"]
    no_hits = retriever.search(probes["no_hit_query"], supplier=complete_supplier, limit=12)
    no_hit_check = evaluate_memory(repo, complete_supplier, [], scan_complete=False)
    no_hit_blocks_absence = not no_hits and no_hit_check.business_status is None

    return {
        "dataset_kind": source["dataset_kind"], "case_id": repo.case_id,
        "snapshot_id": repo.snapshot_id,
        "retrieval": {
            "recall_at_12": {"numerator": total_found, "denominator": total_relevant},
            "supplier_scope": {"numerator": scoped_queries, "denominator": len(queries)},
            "queries": queries,
        },
        "guardrails": {
            "invalid_reference_rejected": _guardrail_score(invalid_reference_rejected),
            "partial_coverage_blocks_status": _guardrail_score(partial_coverage_blocks_status),
            "no_hit_blocks_absence": _guardrail_score(no_hit_blocks_absence),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = evaluate_dataset(args.dataset)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
