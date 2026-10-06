"""Delivery preserves measured bytes and refuses unsafe imports or secret exports."""

import importlib
import json
import os
import shutil
import stat
from copy import copy
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from evals.candidate_corpus import build_corpus

REPO = Path(__file__).resolve().parents[2]


def module():
    assert importlib.util.find_spec("evals.delivery_bundle"), "delivery bundle is not implemented"
    return importlib.import_module("evals.delivery_bundle")


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


@pytest.fixture(scope="module")
def fixture_repo(tmp_path_factory):
    delivery = module()
    root = tmp_path_factory.mktemp("delivery-source")
    for name in delivery.FIXED_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded({"fixture": True}) if target.suffix == ".json" else b"fixture\n")
    for name in ("evals/acceptance/candidate_blueprints.py", "evals/acceptance/candidate_gold.json"):
        shutil.copyfile(REPO / name, root / name)
    shutil.copytree(REPO / "backend/app", root / "backend/app", ignore=shutil.ignore_patterns("__pycache__"))
    font = Path(os.environ.get("DOCUCHECK_CJK_FONT", "C:/Windows/Fonts/simhei.ttf"))
    assert font.is_file(), "CJK font is required for delivery tests"
    corpus = root / "data/m5-candidates"
    build_corpus(corpus, font_path=font)
    for source in (corpus / "inputs/m5-c06").iterdir():
        shutil.copyfile(source, root / delivery.BOUNDARY_DIR / "inputs" / source.name)
    corpus_sha = sha256((corpus / "corpus.json").read_bytes()).hexdigest()
    review = {"independent_human_review": False, "formal_independent_acceptance": False,
              "independently_sealed_packs": 0, "known_author_exposure": True,
              "corpus_sha256": corpus_sha}
    (root / delivery.REVIEW).write_bytes(encoded(review))
    app = {p.relative_to(root / "backend/app").as_posix(): sha256(p.read_bytes()).hexdigest()
           for p in sorted((root / "backend/app").rglob("*.py"))}
    source_sha = sha256(json.dumps(app, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    history = [{"folder": folder,
                "report_sha256": sha256((root / folder / "report.json").read_bytes()).hexdigest(),
                "provenance_sha256": sha256((root / folder / "execution-provenance.json").read_bytes()).hexdigest()}
               for folder in delivery.QUALITY_DIRS]
    summary = {"milestone": "M5-B", "formal_independent_acceptance": False,
        "independently_sealed_packs": 0,
        "corpus_baseline": {"corpus_sha256": corpus_sha,
            "review_sha256": sha256((root / delivery.REVIEW).read_bytes()).hexdigest()},
        "code_identity": {"host_source_manifest_sha256": source_sha, "source_manifest": app},
        "raw_artifact_sha256": {f"{delivery.PERFORMANCE_DIR}/m5-c01-05.json":
            sha256((root / delivery.PERFORMANCE_DIR / "m5-c01-05.json").read_bytes()).hexdigest()},
        "quality_version_history": history,
        "earlier_boundary_failures": [{"folder": folder,
            "record_sha256": sha256((root / folder / "followup-failure-recovered.json").read_bytes()).hexdigest()}
            for folder in delivery.EARLIER_DIRS]}
    (root / delivery.SUMMARY).write_bytes(encoded(summary))
    # Non-whitelisted material must neither be read nor exported.
    (root / ".env").write_text("private-fixture", encoding="utf-8")
    (root / "data/uploads").mkdir()
    (root / "data/uploads/private.pdf").write_bytes(b"private")
    return root


@pytest.fixture(scope="module")
def valid_archive(fixture_repo, tmp_path_factory):
    path = tmp_path_factory.mktemp("delivery-archive") / "valid.zip"
    module().build_bundle(fixture_repo, path)
    return path


def rewrite_archive(source, target, mutate):
    with ZipFile(source) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    entries = mutate(entries)
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for info, data in entries:
            archive.writestr(copy(info), data)


def test_default_cli_only_previews(fixture_repo, tmp_path, capsys):
    target = tmp_path / "not-created.zip"
    assert module().main(["--repo", str(fixture_repo), "--output", str(target)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "preview" and not target.exists()
    assert result["formal_independent_acceptance"] is False
    assert result["independently_sealed_packs"] == 0
    assert not any("uploads" in row["path"] or ".env" in row["path"] for row in result["files"])


def test_archive_restores_original_bytes_and_keeps_failed_task(valid_archive, tmp_path):
    delivery = module()
    checked = delivery.verify_bundle(valid_archive)
    assert checked["verified"] and checked["formal_independent_acceptance"] is False
    destination = tmp_path / "restored"
    restored = delivery.restore_bundle(valid_archive, destination)
    assert restored["restored"]
    with ZipFile(valid_archive) as archive:
        for name in archive.namelist():
            assert (destination / name).read_bytes() == archive.read(name)
    assert (destination / delivery.PERFORMANCE_DIR / "m5-c01-05.json").is_file()
    assert not (destination / ".env").exists()


def test_existing_archive_and_restore_directory_are_never_overwritten(fixture_repo, valid_archive, tmp_path):
    delivery = module()
    target = tmp_path / "preserved.zip"
    target.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        delivery.build_bundle(fixture_repo, target)
    assert target.read_bytes() == b"preserve"
    existing = tmp_path / "already-there"
    existing.mkdir()
    (existing / "keep").write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        delivery.restore_bundle(valid_archive, existing)
    assert (existing / "keep").read_bytes() == b"preserve"


@pytest.mark.parametrize("change", ["missing", "changed", "credential"])
def test_invalid_source_is_rejected_before_archive_creation(fixture_repo, tmp_path, change):
    delivery = module()
    root = tmp_path / "source"
    shutil.copytree(fixture_repo, root)
    report = root / delivery.PERFORMANCE_DIR / "m5-c01-05.json"
    if change == "missing":
        report.unlink()
    elif change == "changed":
        report.write_bytes(encoded({"altered": True}))
    else:
        report.write_bytes(encoded({"nested": {"access_token": "private-fixture"}}))
        summary = json.loads((root / delivery.SUMMARY).read_bytes())
        summary["raw_artifact_sha256"][report.relative_to(root).as_posix()] = sha256(report.read_bytes()).hexdigest()
        (root / delivery.SUMMARY).write_bytes(encoded(summary))
    archive = tmp_path / "must-not-exist.zip"
    with pytest.raises(ValueError):
        delivery.build_bundle(root, archive)
    assert not archive.exists()


@pytest.mark.parametrize("name", ["../escape.json", "/absolute.json", "C:/escape.json",
                                  "data\\escape.json", "data/uploads/secret.json",
                                  "docs/CON.md", "docs/trailing.md.", "unknown.json"])
def test_unsafe_or_unknown_zip_entry_is_rejected_without_restore(valid_archive, tmp_path, name):
    target = tmp_path / "bad.zip"
    rewrite_archive(valid_archive, target, lambda entries: [*entries, (ZipInfo(name), b"{}")])
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError):
        module().restore_bundle(target, destination)
    assert not destination.exists()


@pytest.mark.parametrize("mutation", ["content", "case_collision", "symlink", "credential",
                                      "independent_claim", "size_limit", "duplicate_manifest"])
def test_tampering_is_rejected(valid_archive, tmp_path, mutation):
    delivery = module()
    target = tmp_path / "bad.zip"

    def mutate(entries):
        manifest_entry = next(item for item in entries if item[0].filename == "bundle-manifest.json")
        manifest = json.loads(manifest_entry[1])
        name = delivery.SUMMARY
        if mutation == "case_collision":
            return [*entries, (ZipInfo(name.upper()), b"{}")]
        if mutation == "duplicate_manifest":
            return [*entries, manifest_entry]
        if mutation == "independent_claim":
            manifest["formal_independent_acceptance"] = True
        if mutation == "size_limit":
            manifest["files"][0]["bytes"] = delivery.MAX_FILE_BYTES + 1
        replacement = encoded({"nested": {"api_key": "private-fixture"}}) if mutation == "credential" else b"tampered"
        if mutation == "credential":
            row = next(row for row in manifest["files"] if row["path"] == name)
            row.update(bytes=len(replacement), sha256=sha256(replacement).hexdigest())
        changed = []
        for info, data in entries:
            if info.filename == "bundle-manifest.json":
                data = encoded(manifest)
            elif info.filename == name:
                if mutation in {"content", "credential"}:
                    data = replacement
                elif mutation == "symlink":
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
            changed.append((info, data))
        return changed

    if mutation == "duplicate_manifest":
        with pytest.warns(UserWarning, match="Duplicate name"):
            rewrite_archive(valid_archive, target, mutate)
    else:
        rewrite_archive(valid_archive, target, mutate)
    with pytest.raises(ValueError):
        delivery.verify_bundle(target)


def test_original_changed_source_code_cannot_be_labeled_as_measured_version(fixture_repo, tmp_path):
    root = tmp_path / "source"
    shutil.copytree(fixture_repo, root)
    source = root / "backend/app/agent/graph.py"
    source.write_bytes(source.read_bytes() + b"\n# changed after measurement\n")
    with pytest.raises(ValueError, match="source_version_mismatch"):
        module().preview_bundle(root)


@pytest.mark.parametrize(("key", "value"), [
    ("rule_version", "fields-v5"), ("formal_independent_acceptance", True),
    ("independently_sealed_packs", 6), ("app_file_count", 77),
    ("python_requirement", ">=3.13"), ("historical_results_are_not_new_inference", False),
    ("formal_independent_acceptance", 0), ("formal_independent_acceptance", 0.0),
    ("historical_results_are_not_new_inference", 1), ("historical_results_are_not_new_inference", 1.0),
    ("source_manifest", None),
])
def test_self_consistent_zip_hashes_do_not_allow_contradictory_version_metadata(
    valid_archive, tmp_path, key, value,
):
    delivery = module()
    target = tmp_path / "contradictory-version.zip"

    def mutate(entries):
        contents = {info.filename: data for info, data in entries}
        name = delivery.SUMMARY if key == "source_manifest" else "versions.json"
        changed = json.loads(contents[name])
        if key == "source_manifest":
            sources = changed["code_identity"]["source_manifest"]
            sources[next(iter(sources))] = "0" * 64
        else:
            changed[key] = value
        contents[name] = encoded(changed)
        manifest = json.loads(contents[delivery.MANIFEST])
        row = next(row for row in manifest["files"] if row["path"] == name)
        row.update(bytes=len(contents[name]), sha256=sha256(contents[name]).hexdigest())
        contents[delivery.MANIFEST] = encoded(manifest)
        return [(info, contents[info.filename]) for info, _ in entries]

    rewrite_archive(valid_archive, target, mutate)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError):
        delivery.restore_bundle(target, destination)
    assert not destination.exists()


@pytest.mark.parametrize("key,value", [
    ("schema_version", True), ("schema_version", 1.0),
    ("independently_sealed_packs", False), ("independently_sealed_packs", 0.0),
    ("formal_independent_acceptance", 0), ("formal_independent_acceptance", 0.0),
])
def test_manifest_rejects_boolean_and_numeric_type_confusion(valid_archive, tmp_path, key, value):
    delivery = module()
    target = tmp_path / "incorrect-types.zip"

    def mutate(entries):
        changed = []
        for info, data in entries:
            if info.filename == delivery.MANIFEST:
                manifest = json.loads(data)
                manifest[key] = value
                data = encoded(manifest)
            changed.append((info, data))
        return changed

    rewrite_archive(valid_archive, target, mutate)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError):
        delivery.restore_bundle(target, destination)
    assert not destination.exists()
