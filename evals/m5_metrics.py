"""Score API observations against fixed gold, without running product rules.

Development measurements remain development measurements even when thresholds
pass. Original-source maps are built only on the scoring side of the boundary.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook
from pypdf import PdfReader

from backend.app.agent.answer import FIELD_NAMES

from .candidate_corpus import InputManifest, ProposedGold, _safe, runtime_manifest

NO_RESULT = "未完成或无正式结果"
DETERMINATE = {"满足", "不满足", "资料冲突"}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def original_texts(pack_dir: Path) -> dict[tuple, str]:
    """Read verified originals for evaluation; never supply this map to Agent."""
    manifest = InputManifest.model_validate(runtime_manifest(pack_dir))
    result = {}
    for document in manifest.documents:
        path = _safe(pack_dir, document.filename)
        if path.suffix == ".pdf":
            for number, page in enumerate(PdfReader(path).pages, 1):
                result[(document.filename, number, None, None)] = page.extract_text() or ""
        else:
            workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
            try:
                for sheet in workbook:
                    for row in sheet:
                        for cell in row:
                            if cell.value is not None:
                                result[(document.filename, None, sheet.title, cell.coordinate)] = str(cell.value)
            finally:
                workbook.close()
    return result


def _location(source: dict) -> tuple:
    return (source.get("document_name"), source.get("page"), source.get("sheet"), source.get("cell"))


def _visible(answer: str) -> dict[tuple, list[str]]:
    names = {label: str(field) for field, label in FIELD_NAMES.items()}
    pairs = defaultdict(list)
    for line in answer.splitlines():
        match = re.match(r"^(.+?) · (.+?)：(.+?)。", line)
        if match and match[2] in names:
            pairs[(match[1], names[match[2]])].append(match[3])
    return pairs


def score_pack(manifest: InputManifest, gold: ProposedGold, observation: dict,
               *, source_texts: dict[tuple, str]) -> dict:
    """Treat a corrupt observation as failed, preserving planned gold cells."""
    expected_pairs = {(s.id, str(field)) for s in manifest.suppliers for field in FIELD_NAMES}
    if ({(r.supplier, str(r.field_key)) for r in gold.results} != expected_pairs
            or any(r.model != next(s.model for s in manifest.suppliers if s.id == r.supplier)
                   for r in gold.results)):
        raise ValueError("evaluation_gold_scope")
    try:
        return _score_pack(manifest, gold, observation, source_texts=source_texts)
    except (TypeError, KeyError, AttributeError):
        # Keep readable claims/citations for error accounting; promote nothing.
        run = observation.get("run") if isinstance(observation, dict) else None
        answer = run.get("answer_text") if isinstance(run, dict) else None
        exported = observation.get("export") if isinstance(observation, dict) else None
        result = exported.get("result") if isinstance(exported, dict) else None
        checks = result.get("checks") if isinstance(result, dict) else None
        salvaged = []
        for check in checks if isinstance(checks, list) else []:
            if not isinstance(check, dict):
                continue
            value = {k: v if isinstance(v, str) else None for k, v in check.items()
                     if k in {"supplier", "field_key", "business_status"}}
            for key in ("evidence_refs", "reason_evidence_refs"):
                refs = check.get(key)
                value[key] = [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else []
            salvaged.append(value)
        report = _score_pack(manifest, gold, {"pack_id": manifest.pack_id,
                             "error_code": "invalid_observation", "run": {
                                 "answer_text": answer if isinstance(answer, str) else ""},
                             "export": {"result": {"checks": salvaged}}}, source_texts=source_texts)
        report["errors"].append("invalid_observation")
        return report


def _score_pack(manifest: InputManifest, gold: ProposedGold, observation: dict,
                *, source_texts: dict[tuple, str]) -> dict:
    if manifest.pack_id != gold.pack_id or observation.get("pack_id") != manifest.pack_id:
        raise ValueError("evaluation_pack_scope")
    expected = {(r.supplier, str(r.field_key)): r for r in gold.results}
    if len(expected) != len(gold.results):
        raise ValueError("evaluation_gold_duplicate")
    run = observation.get("run") or {}
    exported = observation.get("export") or {}
    result = exported.get("result") or {}
    errors = []
    export_scope = (bool(observation.get("case_id") and observation.get("snapshot_id")
                         and observation.get("run_id"))
                    and result.get("case_id") == run.get("case_id") == observation.get("case_id")
                    and result.get("snapshot_id") == run.get("snapshot_id") == observation.get("snapshot_id")
                    and result.get("run_id") == run.get("id") == observation.get("run_id"))
    current = (export_scope and result.get("is_current") is True and run.get("is_current") is True
               and result.get("run_status") == run.get("status") == "completed")
    documents = observation.get("documents") or []
    wanted = {d.filename: d for d in manifest.documents}
    doc_ids = Counter(d.get("id") for d in documents)
    doc_names = Counter(d.get("original_name") for d in documents)
    by_id = {d.get("id"): d for d in documents if doc_ids[d.get("id")] == 1}

    def valid_doc(document: dict | None) -> bool:
        if not document:
            return False
        name = document.get("original_name")
        expected_doc = wanted.get(name)
        return bool(expected_doc and doc_names[name] == 1
                    and document.get("source_sha256") == expected_doc.sha256
                    and document.get("role") == expected_doc.role
                    and document.get("supplier_handle") == expected_doc.supplier
                    and document.get("model_name") == expected_doc.model)

    documents_valid = (len(documents) == len(wanted) and len(by_id) == len(wanted)
                       and all(valid_doc(d) for d in documents))
    if not documents_valid:
        errors.append("document_binding")
    checks = result.get("checks") or []
    grouped = defaultdict(list)
    for check in checks:
        grouped[(check.get("supplier"), check.get("field_key"))].append(check)
    if set(grouped) - set(expected):
        errors.append("unplanned_checks")
    sources = exported.get("sources") or []
    source_ids = Counter(s.get("evidence_id") for s in sources)
    by_source = {s.get("evidence_id"): s for s in sources if source_ids[s.get("evidence_id")] == 1}
    visible = _visible(run.get("answer_text") or "")
    citation_total = citation_errors = 0

    def valid_ref(ref: str, supplier: str) -> bool:
        source = by_source.get(ref)
        if not source or not export_scope:
            return False
        page, sheet, cell = source.get("page"), source.get("sheet"), source.get("cell")
        if not ((type(page) is int and page > 0 and sheet is None and cell is None)
                or (page is None and isinstance(sheet, str) and isinstance(cell, str))):
            return False
        document = by_id.get(source.get("document_id"))
        original = source_texts.get(_location(source))
        return bool(valid_doc(document)
                    and source.get("document_name") == document.get("original_name")
                    and document.get("supplier_handle") in {None, supplier}
                    and original and isinstance(source.get("source_text"), str)
                    and _compact(source["source_text"])
                    and _compact(source["source_text"]) in _compact(original))

    def refs_for(check: dict) -> set[str]:
        return set(check.get("evidence_refs") or ()) | set(check.get("reason_evidence_refs") or ())

    for check in checks:
        refs = refs_for(check)
        citation_total += len(refs)
        citation_errors += sum(not valid_ref(ref, check.get("supplier")) for ref in refs)
    cells, confusion = [], defaultdict(Counter)
    for pair, reference in expected.items():
        candidates = grouped.get(pair, [])
        check = candidates[0] if len(candidates) == 1 else None
        formal = (current and documents_valid and check is not None
                  and check.get("snapshot_id") == observation.get("snapshot_id")
                  and check.get("processing_status") == "complete" and check.get("is_formal") is True)
        if reference.absence_documents:
            formal = formal and all(any(d.get("original_name") == name
                                         and d.get("parse_status") == "complete" for d in documents)
                                    for name in reference.absence_documents)
        actual = check.get("business_status") if formal else NO_RESULT
        correct = actual == reference.business_status and visible.get(pair) == [reference.business_status]
        if actual == reference.business_status and not correct:
            actual = "可见答复不一致"
        refs = refs_for(check) if check else set()
        located = formal and all(any(
            valid_ref(ref, reference.supplier)
            and _location(by_source[ref]) == (a.document, a.page, a.sheet, a.cell)
            and _compact(a.text) in _compact(by_source[ref]["source_text"])
            for ref in refs) for a in reference.evidence)
        dangerous = (reference.business_status != "满足"
                     and ("满足" in visible.get(pair, [])
                          or any(c.get("business_status") == "满足" for c in candidates)))
        confusion[reference.business_status][actual or NO_RESULT] += 1
        cells.append({"supplier": pair[0], "field": pair[1], "expected": reference.business_status,
                      "actual": actual, "status_correct": correct,
                      "necessary_evidence_located": bool(located), "dangerous_meet": dangerous,
                      "failure": (None if correct else observation.get("error_code") or
                                  run.get("error_code") or "missing_or_invalid_result")})
    determinate = [c for c in cells if c["expected"] in DETERMINATE]
    correct_count = sum(c["status_correct"] for c in cells)
    return {"pack_id": manifest.pack_id, "formal_independent_acceptance": False,
            "status_accuracy": {"numerator": correct_count, "denominator": len(cells)},
            "necessary_evidence": {"numerator": sum(c["necessary_evidence_located"] for c in determinate),
                                   "denominator": len(determinate)},
            "dangerous_meets": sum(c["dangerous_meet"] for c in cells),
            "citation_errors": {"numerator": citation_errors, "denominator": citation_total},
            "citation_error_rate": citation_errors / citation_total if citation_total else None,
            "task_success": (correct_count == len(cells) and all(c["necessary_evidence_located"] for c in cells)
                             and citation_errors == 0 and not errors),
            "confusion_matrix": {k: dict(v) for k, v in confusion.items()}, "cells": cells, "errors": errors}


def aggregate_scores(scores: list[dict]) -> dict:
    def ratio(key):
        return {part: sum(s[key][part] for s in scores) for part in ("numerator", "denominator")}
    confusion = defaultdict(Counter)
    for score in scores:
        for expected, observed in score["confusion_matrix"].items():
            confusion[expected].update(observed)
    citations = ratio("citation_errors")
    return {"scope": "ai_reviewed_development_corpus", "formal_independent_acceptance": False,
            "status_accuracy": ratio("status_accuracy"), "necessary_evidence": ratio("necessary_evidence"),
            "dangerous_meets": sum(s["dangerous_meets"] for s in scores),
            "agent_tasks": {"numerator": sum(s["task_success"] for s in scores), "denominator": len(scores)},
            "citation_errors": citations,
            "citation_error_rate": citations["numerator"] / citations["denominator"] if citations["denominator"] else None,
            "confusion_matrix": {k: dict(v) for k, v in confusion.items()}, "packs": scores}
