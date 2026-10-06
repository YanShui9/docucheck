"""Offline, human-declared review and immutable local freeze records.

Checks declarations and version consistency; cannot authenticate a reviewer or
prove independent human work. No model calls, .env, or production services.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from .candidate_corpus import (
    CorpusIndex,
    ProposedGold,
    StrictModel,
    _safe,
    validate_corpus,
)

REPO = Path(__file__).resolve().parents[1]
SHA = r"^[a-f0-9]{64}$"
COLUMNS = ("pack", "supplier", "field", "proposed_status", "review_decision", "reviewed_status",
           "reviewer", "reviewed_on", "comment")


class Confirmation(StrictModel):
    schema_version: Literal[1]
    state: Literal["pending", "confirmed"]
    corpus_sha256: str = Field(pattern=SHA)
    snapshot_sha256: str = Field(pattern=SHA)
    reviewer: str = Field(max_length=120)
    reviewed_on: str = Field(max_length=10)
    independent_human_review: bool | None = Field(strict=True)
    author_seen_acknowledged: bool | None = Field(strict=True)
    holdout_used_for_tuning: bool | None = Field(strict=True)
    holdout_packs: list[str] = Field(max_length=1000)
    contamination_history: str = Field(max_length=2000)


def _bytes(path: Path) -> bytes:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("review_file_limit")
    return path.read_bytes()


def _hash(path: Path) -> str:
    return sha256(_bytes(path)).hexdigest()


def _encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write(path: Path, value) -> None:
    with path.open("xb") as stream:
        stream.write(_encoded(value))


def _code_baseline() -> dict:
    paths = sorted({p.relative_to(REPO).as_posix() for folder in ("backend", "evals")
                    for p in (REPO / folder).rglob("*.py") if not any(s.startswith(".env") for s in p.parts)})
    paths += ["pyproject.toml", "backend/environment.yml"]
    files = [{"path": name, "sha256": _hash(_safe(REPO, name))} for name in sorted(paths)]
    return {"kind": "python_sources_and_dependency_config", "files": files,
            "sha256": sha256(_encoded(files)).hexdigest()}


def _expected(root: Path):
    index = CorpusIndex.model_validate_json(_bytes(_safe(root, "corpus.json"))).model_dump()
    results = {}
    for entry in index["packs"]:
        gold = ProposedGold.model_validate_json(_bytes(_safe(root, f"gold/{entry['id']}.json")))
        for result in gold.results:
            results[(entry["id"], result.supplier, result.field_key.value)] = result.business_status
    return index, results


def prepare_review(root: Path, directory: Path) -> None:
    """Prepare blank records in a new directory; never fill human declarations."""
    report = validate_corpus(root)
    if report["errors"]:
        raise ValueError("corpus_invalid")
    index, expected = _expected(root)
    directory.mkdir(parents=True, exist_ok=False)
    snapshot = {"schema_version": 1, "corpus_sha256": _hash(_safe(root, "corpus.json")),
                "packs": index["packs"], "code_baseline": _code_baseline(),
                "known_exposure": "Agent authored and inspected the synthetic candidates; no independent blind-data claim."}
    _write(directory / "snapshot.json", snapshot)
    confirmation = Confirmation(
        schema_version=1, state="pending", corpus_sha256=snapshot["corpus_sha256"],
        snapshot_sha256=_hash(directory / "snapshot.json"), reviewer="", reviewed_on="",
        independent_human_review=None, author_seen_acknowledged=None, holdout_used_for_tuning=None,
        holdout_packs=[e["id"] for e in index["packs"] if e["partition"] == "holdout_candidate"],
        contamination_history="",
    )
    _write(directory / "confirmation.json", confirmation.model_dump())
    with (directory / "review.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for (pack, supplier, field), status in expected.items():
            writer.writerow({"pack": pack, "supplier": supplier, "field": field, "proposed_status": status,
                             "review_decision": "pending", "reviewed_status": "", "reviewer": "",
                             "reviewed_on": "", "comment": ""})
    (directory / "README.md").write_text(
        "# M5-A 独立人工复核\n\n"
        "本目录由程序准备，尚未复核或封存。请独立打开原件，不能仅按拟定答案批量确认。\n\n"
        f"[逐格原文及定位]({(root / 'review/review-guide.md').resolve().as_posix()})\n\n"
        "1. 核对104格原文、适用供应商/型号、比较口径、冲突双方和缺失完整范围。\n"
        "2. 填review.csv：review_decision=accepted/changed、reviewed_status=核对后的中文状态、"
        "reviewer=真实复核人、reviewed_on=YYYY-MM-DD、comment=意见。未核对的格保留pending。\n"
        "3. 若拟定标签有错，先反馈具体包/供应商/字段、原文和建议状态；修订gold、定位及清单后重新准备复核，"
        "旧版本确认不能复用。changed状态与当前gold不一致时工具拒绝冻结。\n"
        "4. 完成后再填写confirmation.json：state=confirmed；姓名/日期与CSV一致；"
        "independent_human_review=true；author_seen_acknowledged=true；holdout_used_for_tuning=false；"
        "contamination_history如实说明资料使用历史。保留已生成的哈希和六包留出名单。\n"
        "5. 用户明确确认后才显式运行--freeze，再用--seal检查freeze.json。任何被绑定文件改动使旧记录失效。\n\n"
        "这些是可核验的本机声明，不是身份认证、数字签名或业务正确性测量。作者已见合成候选，不能宣称第三方盲测。\n",
        encoding="utf-8",
    )


def _valid_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
        return parsed.isoformat() == value and parsed <= datetime.now(ZoneInfo("Asia/Shanghai")).date()
    except ValueError:
        return False


def review_report(root: Path, directory: Path, *, seal_path: Path | None = None) -> dict:
    """Count only complete, version-matching human declarations."""
    report = {"scope": "human_declared_review_gate", "errors": [], "pending_rows": 0,
              "reviewed_packs": 0, "sealed_packs": 0, "ready_to_freeze": False,
              "business_acceptance_measured": False, "identity_authenticated": False}
    errors = report["errors"]
    def error(code):
        errors.append({"code": code})

    def invalid_seal():
        if seal_path is not None:
            error("seal_changed")
        return report

    asset_report = validate_corpus(root)
    if asset_report["errors"]:
        error("corpus_invalid")
        return invalid_seal()
    try:
        index, expected = _expected(root)
        snapshot_path = _safe(directory, "snapshot.json")
        confirmation_path = _safe(directory, "confirmation.json")
        csv_path = _safe(directory, "review.csv")
        snapshot = json.loads(_bytes(snapshot_path))
        confirmation = Confirmation.model_validate_json(_bytes(confirmation_path))
        if (confirmation.corpus_sha256 != _hash(_safe(root, "corpus.json"))
                or confirmation.snapshot_sha256 != _hash(snapshot_path)
                or snapshot["corpus_sha256"] != confirmation.corpus_sha256
                or snapshot["packs"] != index["packs"]
                or snapshot["code_baseline"]["sha256"] != sha256(_encoded(snapshot["code_baseline"]["files"])).hexdigest()):
            error("review_version")
            return invalid_seal()
        reader = csv.DictReader(io.StringIO(_bytes(csv_path).decode("utf-8-sig"), newline=""))
        if reader.fieldnames != list(COLUMNS):
            raise ValueError("csv_columns")
        rows = list(reader)
        if len(rows) > 10000 or any(set(row) != set(COLUMNS) or None in row.values() for row in rows):
            raise ValueError("csv_structure")
    except (ValueError, OSError, KeyError, TypeError, csv.Error):
        error("review_schema")
        return invalid_seal()

    keys = [(r["pack"], r["supplier"], r["field"]) for r in rows]
    multiplicities = Counter(keys)
    if len(keys) != len(expected) or set(keys) != set(expected) or any(n != 1 for n in multiplicities.values()):
        error("review_coverage")
    accepted = set()
    for row, key in zip(rows, keys, strict=True):
        if key not in expected or multiplicities[key] != 1:
            continue
        if row["proposed_status"] != expected[key]:
            error("review_status")
            continue
        if row["review_decision"] == "pending":
            report["pending_rows"] += 1
            continue
        before = len(errors)
        if row["review_decision"] not in {"accepted", "changed"}:
            error("review_decision")
        if row["reviewed_status"] != expected[key]:
            error("gold_revision_required" if row["review_decision"] == "changed" else "review_status")
        if row["review_decision"] == "changed" and not row["comment"].strip():
            error("change_comment")
        if not row["reviewer"].strip() or row["reviewer"] != confirmation.reviewer:
            error("review_identity")
        if not _valid_date(row["reviewed_on"]) or row["reviewed_on"] != confirmation.reviewed_on:
            error("review_date")
        if len(errors) == before:
            accepted.add(key)
    holdouts = {e["id"] for e in index["packs"] if e["partition"] == "holdout_candidate"}
    if (set(confirmation.holdout_packs) != holdouts or len(confirmation.holdout_packs) != len(holdouts)
            or len(holdouts) < 6):
        error("holdout_scope")
    if confirmation.state == "confirmed":
        if (not confirmation.reviewer.strip() or not _valid_date(confirmation.reviewed_on)
                or confirmation.independent_human_review is not True
                or confirmation.author_seen_acknowledged is not True):
            error("confirmation_required")
        if confirmation.holdout_used_for_tuning is not False:
            error("contaminated_holdout")
        if not confirmation.contamination_history.strip():
            error("contamination_history_required")
        if len(accepted) != len(expected):
            error("review_incomplete")
    complete_packs = {e["id"] for e in index["packs"]
                      if {k for k in expected if k[0] == e["id"]}.issubset(accepted)}
    report["reviewed_packs"] = len(complete_packs) if not errors and confirmation.state == "confirmed" else 0
    report["ready_to_freeze"] = not errors and len(accepted) == len(expected) and confirmation.state == "confirmed"
    if seal_path is not None:
        try:
            # Deterministic expected bytes bind the full review version; even editing
            # formatting after freezing invalidates this local record.
            if (seal_path.resolve().parent != directory.resolve() or not report["ready_to_freeze"]
                    or _bytes(_safe(directory, seal_path.name)) != _encoded(_freeze_record(root, directory, confirmation))):
                error("seal_changed")
            else:
                report["sealed_packs"] = len(holdouts)
        except (ValueError, OSError):
            error("seal_changed")
    if errors:
        report["ready_to_freeze"] = False
    return report


def _freeze_record(root: Path, directory: Path, confirmation: Confirmation):
    return {"schema_version": 1, "kind": "local_human_declared_freeze_not_signature",
            "corpus_sha256": _hash(_safe(root, "corpus.json")),
            "snapshot_sha256": _hash(_safe(directory, "snapshot.json")),
            "review_csv_sha256": _hash(_safe(directory, "review.csv")),
            "confirmation_sha256": _hash(_safe(directory, "confirmation.json")),
            "reviewer": confirmation.reviewer, "reviewed_on": confirmation.reviewed_on,
            "holdout_packs": confirmation.holdout_packs, "known_author_exposure": True}


def freeze_corpus(root: Path, directory: Path) -> None:
    report = review_report(root, directory)
    if not report["ready_to_freeze"]:
        raise ValueError("review_not_ready")
    confirmation = Confirmation.model_validate_json(_bytes(_safe(directory, "confirmation.json")))
    _write(directory / "freeze.json", _freeze_record(root, directory, confirmation))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/m5-candidates"))
    parser.add_argument("--review-dir", type=Path, default=Path("data/m5-review"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--freeze", action="store_true")
    parser.add_argument("--seal", type=Path, help="Verify a freeze record inside the review directory")
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare_review(args.root, args.review_dir)
        if args.freeze:
            freeze_corpus(args.root, args.review_dir)
        report = review_report(args.root, args.review_dir, seal_path=args.seal)
    except (ValueError, OSError) as exc:
        print(json.dumps({"errors": [{"code": str(exc)}], "ready_to_freeze": False}))
        raise SystemExit(1) from exc
    print(json.dumps(report, ensure_ascii=False))
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
