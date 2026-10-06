"""Preview, archive, verify and restore explicitly selected M5 development evidence.

No .env, model calls, database access or upload-directory traversal. Hashes prove
byte integrity, not authenticity: keep the archive SHA-256 in a trusted channel.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import stat
import unicodedata
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .candidate_corpus import CorpusIndex, InputManifest, validate_corpus

REPO = Path(__file__).resolve().parents[1]
SUMMARY = "evals/reports/m5-b-development-2026-10-03.json"
REVIEW = "evals/acceptance/development_review_2026_10_02.json"
CORPUS = "data/m5-candidates"
MANIFEST = "bundle-manifest.json"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_FILES = 1000
PACKS = tuple(f"m5-{prefix}{number:02d}" for prefix in "ch" for number in range(1, 7))
QUALITY_DIRS = tuple(f"data/m5-b-live-quality-20261002-{suffix}" for suffix in (
    "fields-v2", "fields-v4", "fields-v4-progress", "memory-source", "finalization", "source-handles"))
PERFORMANCE_DIR = "data/m5-b-live-performance-20261002-source-handles"
BOUNDARY_DIR = "data/m5-b-live-boundaries-20261002-source-handles"
EARLIER_DIRS = tuple(f"data/m5-b-live-boundaries-20261002-{s}" for s in ("finalization", "context"))
GROUP_META = ("execution-driver.json", "execution-provenance.json", "protocol.json", "report.json")
LOCK_FILES = ("backend/requirements-dev.lock.txt", "backend/requirements-windows.lock.txt",
              "backend/requirements-worker.lock.txt", "backend/requirements-worker.txt",
              "backend/environment.yml", "pyproject.toml", "frontend/package.json", "frontend/package-lock.json")
FIXED_FILES = frozenset([
    SUMMARY, REVIEW, *LOCK_FILES,
    "evals/acceptance/candidate_blueprints.py", "evals/acceptance/candidate_gold.json",
    "evals/acceptance/source_catalog.json", "evals/acceptance/community_repository_snapshot.json",
    "docs/reproduction.md", "docs/backup-and-recovery.md", "docs/interview-demo.md",
    "docs/architecture-and-interview.md", "docs/m5-b-development-validation-2026-10-03.md",
    "ops/compose.restore.yaml",
    *(f"{folder}/{name}" for folder in QUALITY_DIRS for name in (*GROUP_META, *(f"{p}-01.json" for p in PACKS))),
    *(f"{PERFORMANCE_DIR}/{name}" for name in (*GROUP_META, "execution-recovery.json",
                                              *(f"m5-c01-{n:02d}.json" for n in range(1, 11)))),
    *(f"{BOUNDARY_DIR}/{name}" for name in (
        "correction-receipt.json", "correction-source-run.json", "correction.json", "execution-provenance.json",
        "followup-receipt.json", "followup-run.json", "followup.json", "protocol.json", "queued-cancel-receipt.json",
        "queued-cancel.json", "report.json", "unknown-supplier-receipt.json", "unknown-supplier.json",
        "inputs/manifest.json", "inputs/requirements.pdf", "inputs/vendor-a-quote.xlsx", "inputs/vendor-a-spec.pdf")),
    *(f"{folder}/{name}" for folder in EARLIER_DIRS
      for name in ("followup-failure-recovered.json", "protocol.json", "report.json")),
    f"{EARLIER_DIRS[1]}/followup-receipt.json",
    "data/m5-b-retrieval-source-handles-20261003/protocol.json",
    "data/m5-b-retrieval-source-handles-20261003/report.json",
    *(f"data/m5-b-source-coverage-20261002/{name}" for name in (
        "service-fields-v4-source-handles.json", "service-source-handles-recovery-20261003.json",
        "service-source-handles-end-20261003.json", "verification-source-handles-20261003.json",
        "report-readback-20261003.json", "pricing-reference-20261002.json")),
])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("formal_independent_acceptance", "historical_results_are_not_new_inference",
                     mode="before", check_fields=False)
    @classmethod
    def exact_boolean(cls, value):
        # Literal bools use equality in Pydantic: 0/1 can otherwise pass strict mode.
        if type(value) is not bool:
            raise ValueError("boolean_type_required")
        return value


class FileRecord(StrictModel):
    path: str = Field(min_length=1, max_length=240)
    bytes: int = Field(ge=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BundleManifest(StrictModel):
    schema_version: int = Field(ge=1, le=1)
    kind: Literal["docucheck_m5_development_evidence"]
    generated_at_utc: str = Field(max_length=50)
    scope: Literal["ai_reviewed_author_seen_development_corpus"]
    formal_independent_acceptance: Literal[False]
    independently_sealed_packs: int = Field(ge=0, le=0)
    files: list[FileRecord] = Field(min_length=1, max_length=MAX_FILES)


class VersionRecord(StrictModel):
    schema_version: int = Field(ge=1, le=1)
    app_source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    app_file_count: int = Field(ge=1, le=MAX_FILES)
    rule_version: Literal["fields-v4"]
    python_requirement: Literal[">=3.12,<3.13"]
    formal_independent_acceptance: Literal[False]
    independently_sealed_packs: int = Field(ge=0, le=0)
    dependency_file_sha256: dict[str, str]
    historical_results_are_not_new_inference: Literal[True]


def _encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _json(data: bytes):
    try:
        return json.loads(data, object_pairs_hook=_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid_json") from error


def _path(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 240:
        raise ValueError("unsafe_bundle_path")
    path = PurePosixPath(name)
    reserved = {"con", "prn", "aux", "nul", *(f"{p}{n}" for p in ("com", "lpt") for n in range(1, 10))}
    if (path.is_absolute() or path.as_posix() != name or "\\" in name or ":" in name
            or unicodedata.normalize("NFC", name) != name
            or any(part in {".", ".."} or part.rstrip(" .") != part
                   or part.split(".")[0].casefold() in reserved or any(ord(c) < 32 for c in part)
                   for part in path.parts)):
        raise ValueError("unsafe_bundle_path")
    return name


def _allowed(name: str) -> bool:
    _path(name)
    if name in FIXED_FILES or name in {MANIFEST, "versions.json", f"{CORPUS}/corpus.json"}:
        return True
    for pack in PACKS:
        if name == f"{CORPUS}/gold/{pack}.json":
            return True
        prefix = f"{CORPUS}/inputs/{pack}/"
        if name.startswith(prefix):
            filename = name[len(prefix):]
            return filename == "manifest.json" or bool(re.fullmatch(
                r"requirements\.pdf|vendor-[a-c]-(quote\.xlsx|spec\.pdf)", filename))
    return False


def _credentials(value, depth: int = 0) -> None:
    if depth > 60:
        raise ValueError("bundle_json_depth_limit")
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.casefold().replace("-", "_")
            if (normalized in {"password", "secret", "api_key", "access_token", "refresh_token", "id_token",
                               "client_secret", "authorization", "cookie", "set_cookie"}
                    or normalized.endswith(("_password", "_api_key"))):
                raise ValueError("credential_field_forbidden")
            _credentials(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _credentials(child, depth + 1)
    elif isinstance(value, str) and (
            re.search(r"[a-z][a-z0-9+.-]*://[^/\s]*@", value, re.IGNORECASE)
            or re.match(r"Bearer\s+\S+", value, re.IGNORECASE)
            or re.search(r"\bsk-[A-Za-z0-9_-]{20,}\b", value)):
        raise ValueError("credential_value_forbidden")


def _no_links(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("filesystem_link_forbidden")


def _read(repo: Path, name: str) -> bytes:
    if not _allowed(name) or name in {MANIFEST, "versions.json"}:
        raise ValueError("unknown_bundle_entry")
    path = repo / name
    _no_links(path)
    if not path.is_file():
        raise ValueError("required_file_missing")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("bundle_file_limit")
    data = path.read_bytes()
    if path.suffix == ".json":
        _credentials(_json(data))
    return data


def _corpus_files(payload: dict[str, bytes]) -> set[str]:
    index = CorpusIndex.model_validate(_json(payload[f"{CORPUS}/corpus.json"]))
    if {entry.id for entry in index.packs} != set(PACKS):
        raise ValueError("corpus_pack_coverage")
    expected = {f"{CORPUS}/corpus.json"}
    for entry in index.packs:
        name = f"{CORPUS}/inputs/{entry.id}/manifest.json"
        gold = f"{CORPUS}/gold/{entry.id}.json"
        expected.update((name, gold))
        if sha256(payload[name]).hexdigest() != entry.manifest_sha256 or sha256(payload[gold]).hexdigest() != entry.gold_sha256:
            raise ValueError("corpus_version_mismatch")
        manifest = InputManifest.model_validate(_json(payload[name]))
        if manifest.pack_id != entry.id:
            raise ValueError("corpus_pack_mismatch")
        for document in manifest.documents:
            name = f"{CORPUS}/inputs/{entry.id}/{document.filename}"
            expected.add(name)
            data = payload[name]
            if len(data) != document.bytes or sha256(data).hexdigest() != document.sha256:
                raise ValueError("corpus_asset_mismatch")
    return expected


def _check_payload(payload: dict[str, bytes]) -> None:
    if not FIXED_FILES.issubset(payload) or "versions.json" not in payload:
        raise ValueError("required_bundle_entries_missing")
    if len(payload) > MAX_FILES or sum(map(len, payload.values())) > MAX_TOTAL_BYTES:
        raise ValueError("bundle_total_limit")
    names = set()
    for name, data in payload.items():
        if name == MANIFEST or not _allowed(name) or name.casefold() in names:
            raise ValueError("unknown_or_duplicate_bundle_entry")
        names.add(name.casefold())
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("bundle_file_limit")
        if name.endswith(".json"):
            _credentials(_json(data))
    try:
        summary, review = (_json(payload[name]) for name in (SUMMARY, REVIEW))
        versions = VersionRecord.model_validate(_json(payload["versions.json"]))
        if (summary["milestone"] != "M5-B" or summary["formal_independent_acceptance"] is not False
                or summary["independently_sealed_packs"] != 0 or review["independent_human_review"] is not False
                or review["formal_independent_acceptance"] is not False or review["independently_sealed_packs"] != 0):
            raise ValueError("independent_claim_forbidden")
        baseline = summary["corpus_baseline"]
        if (sha256(payload[f"{CORPUS}/corpus.json"]).hexdigest() != baseline["corpus_sha256"]
                or review["corpus_sha256"] != baseline["corpus_sha256"]
                or sha256(payload[REVIEW]).hexdigest() != baseline["review_sha256"]
                or versions.app_source_manifest_sha256 != summary["code_identity"]["host_source_manifest_sha256"]):
            raise ValueError("baseline_version_mismatch")
        sources = summary["code_identity"]["source_manifest"]
        if (not isinstance(sources, dict) or len(sources) != versions.app_file_count
                or any(not isinstance(name, str) or not _path(name).endswith(".py")
                       or not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                       for name, value in sources.items())
                or sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                != versions.app_source_manifest_sha256):
            raise ValueError("source_manifest_mismatch")
        if versions.dependency_file_sha256 != {name: sha256(payload[name]).hexdigest() for name in LOCK_FILES}:
            raise ValueError("dependency_version_mismatch")
        if set(payload) != FIXED_FILES | _corpus_files(payload) | {"versions.json"}:
            raise ValueError("unexpected_bundle_entries")
        hashes = dict(summary["raw_artifact_sha256"])
        history = summary["quality_version_history"]
        if len(history) != len(QUALITY_DIRS) or {row["folder"] for row in history} != set(QUALITY_DIRS):
            raise ValueError("history_coverage_missing")
        for row in history:
            hashes[f"{row['folder']}/report.json"] = row["report_sha256"]
            hashes[f"{row['folder']}/execution-provenance.json"] = row["provenance_sha256"]
        for row in summary["earlier_boundary_failures"]:
            if row["folder"] not in EARLIER_DIRS:
                raise ValueError("unexpected_history_path")
            hashes[f"{row['folder']}/followup-failure-recovered.json"] = row["record_sha256"]
        for name, expected in hashes.items():
            if name not in payload or sha256(payload[name]).hexdigest() != expected:
                raise ValueError("measured_artifact_mismatch")
        boundary = f"{BOUNDARY_DIR}/inputs"
        manifest = InputManifest.model_validate(_json(payload[f"{boundary}/manifest.json"]))
        for doc in manifest.documents:
            data = payload[f"{boundary}/{doc.filename}"]
            if len(data) != doc.bytes or sha256(data).hexdigest() != doc.sha256:
                raise ValueError("boundary_asset_mismatch")
    except (KeyError, TypeError) as error:
        raise ValueError("bundle_baseline_invalid") from error


def prepare_payload(repo: Path) -> dict[str, bytes]:
    """Read only declared sources. No automatic regeneration of measured inputs."""
    payload = {name: _read(repo, name) for name in sorted(FIXED_FILES)}
    summary = _json(payload[SUMMARY])
    source = {}
    for path in sorted((repo / "backend/app").rglob("*.py")):
        _no_links(path)
        source[path.relative_to(repo / "backend/app").as_posix()] = sha256(path.read_bytes()).hexdigest()
    code = summary["code_identity"]
    fingerprint = sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if source != code["source_manifest"] or fingerprint != code["host_source_manifest_sha256"]:
        raise ValueError("source_version_mismatch")
    index_name = f"{CORPUS}/corpus.json"
    payload[index_name] = _read(repo, index_name)
    index = CorpusIndex.model_validate(_json(payload[index_name]))
    for entry in index.packs:
        name = f"{CORPUS}/inputs/{entry.id}/manifest.json"
        payload[name] = _read(repo, name)
        payload[f"{CORPUS}/gold/{entry.id}.json"] = _read(repo, f"{CORPUS}/gold/{entry.id}.json")
        manifest = InputManifest.model_validate(_json(payload[name]))
        for doc in manifest.documents:
            name = f"{CORPUS}/inputs/{entry.id}/{doc.filename}"
            payload[name] = _read(repo, name)
    if validate_corpus(repo / CORPUS)["errors"]:
        raise ValueError("corpus_invalid")
    payload["versions.json"] = _encoded({"schema_version": 1,
        "app_source_manifest_sha256": fingerprint, "app_file_count": len(source),
        "rule_version": "fields-v4", "python_requirement": ">=3.12,<3.13",
        "formal_independent_acceptance": False, "independently_sealed_packs": 0,
        "dependency_file_sha256": {name: sha256(payload[name]).hexdigest() for name in LOCK_FILES},
        "historical_results_are_not_new_inference": True})
    _check_payload(payload)
    return payload


def _manifest(payload: dict[str, bytes]) -> BundleManifest:
    return BundleManifest(schema_version=1, kind="docucheck_m5_development_evidence",
        generated_at_utc=datetime.now(UTC).isoformat(), scope="ai_reviewed_author_seen_development_corpus",
        formal_independent_acceptance=False, independently_sealed_packs=0,
        files=[FileRecord(path=name, bytes=len(data), sha256=sha256(data).hexdigest())
               for name, data in sorted(payload.items())])


def preview_bundle(repo: Path) -> dict:
    result = _manifest(prepare_payload(repo)).model_dump()
    return {"mode": "preview", **result, "file_count": len(result["files"])}


def build_bundle(repo: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("archive_exists")
    _no_links(output)
    payload = prepare_payload(repo)
    manifest = _manifest(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation; no file is changed when preparation or validation fails.
    with output.open("xb") as stream, ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, _encoded(manifest.model_dump()))
        for name, data in sorted(payload.items()):
            archive.writestr(name, data)
    checked = verify_bundle(output)
    return {"mode": "build", **checked}


def _load_bundle(path: Path):
    _no_links(path)
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("bundle_archive_limit")
    raw = path.read_bytes()
    payload = {}
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES + 1 or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
                raise ValueError("bundle_total_limit")
            names = set()
            for info in infos:
                name = info.filename
                if (info.orig_filename != name or not _allowed(name) or name.casefold() in names or info.is_dir()
                        or info.file_size > MAX_FILE_BYTES or info.flag_bits & 1
                        or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                        or stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG}):
                    raise ValueError("unsafe_or_duplicate_zip_entry")
                names.add(name.casefold())
            # Validate the complete inventory before decompressing any member.
            for info in infos:
                name = info.filename
                payload[name] = archive.read(info)
    except (BadZipFile, RuntimeError, EOFError) as error:
        raise ValueError("invalid_zip") from error
    try:
        manifest = BundleManifest.model_validate(_json(payload.pop(MANIFEST)))
    except KeyError as error:
        raise ValueError("bundle_manifest_missing") from error
    if {r.path for r in manifest.files} != set(payload) or len(manifest.files) != len(payload):
        raise ValueError("manifest_entries_mismatch")
    for row in manifest.files:
        data = payload[row.path]
        if row.bytes != len(data) or row.sha256 != sha256(data).hexdigest():
            raise ValueError("bundle_hash_mismatch")
    _check_payload(payload)
    return raw, manifest, payload


def verify_bundle(path: Path) -> dict:
    raw, manifest, payload = _load_bundle(path)
    return {"verified": True, "archive_sha256": sha256(raw).hexdigest(),
            "file_count": len(payload), "total_bytes": sum(map(len, payload.values())),
            "formal_independent_acceptance": manifest.formal_independent_acceptance,
            "independently_sealed_packs": manifest.independently_sealed_packs}


def restore_bundle(path: Path, destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError("restore_destination_exists")
    _no_links(destination)
    raw, manifest, payload = _load_bundle(path)
    # Use already-verified bytes; never reopen/extract an unvalidated replacement ZIP.
    destination.mkdir(parents=True, exist_ok=False)
    marker = destination / ".restore-incomplete"
    marker.write_text("Restore has not completed. Do not use this directory.\n", encoding="utf-8")
    payload[MANIFEST] = _encoded(manifest.model_dump())
    for name, data in sorted(payload.items()):
        target = destination / _path(name)
        _no_links(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
    marker.unlink()
    return {"restored": True, "archive_sha256": sha256(raw).hexdigest(), "file_count": len(payload),
            "formal_independent_acceptance": False, "independently_sealed_packs": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--output", type=Path, default=Path("data/m5-c/docucheck-m5-development.zip"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--build", action="store_true")
    group.add_argument("--verify", type=Path)
    group.add_argument("--restore", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.restore:
            if args.destination is None:
                raise ValueError("restore_destination_required")
            result = restore_bundle(args.restore, args.destination)
        elif args.verify:
            result = verify_bundle(args.verify)
        elif args.build:
            result = build_bundle(args.repo, args.output)
        else:
            result = preview_bundle(args.repo)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except FileExistsError:
        print(json.dumps({"error": "destination_exists"}))
    except (ValueError, OSError):
        # No candidate content, exception chain, credentials or arbitrary paths in CLI errors.
        print(json.dumps({"error": "delivery_validation_failed"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
