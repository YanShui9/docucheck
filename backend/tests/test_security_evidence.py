"""Scanner failures and stale waivers must never become a clean security receipt."""

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.security_evidence import (
    EvidenceError,
    apply_dispositions,
    assess_gitleaks,
    assess_semgrep,
    normalize_findings,
    read_report,
    safe_path,
    snapshot,
    verify_sources,
)


def report(paths=None, **kwargs):
    return {"version": "1", "results": [], "errors": [], "paths": {"scanned": paths or ["app.py"]},
            "skipped_rules": [], **kwargs}


@pytest.mark.parametrize("payload,code", [
    (report(paths=["else.py"]), 0),
    (report(errors=[{"type": "PartialParsing"}]), 0),
    (report(skipped_rules=[{"rule_id": "broken"}]), 0),
    (report(), 2), (report(results={}), 0), (report(paths="app.py"), 0),
    ({}, 0),
])
def test_missing_coverage_errors_or_failed_engine_are_not_success(payload, code):
    with pytest.raises(EvidenceError):
        assess_semgrep(payload, code, {"app.py"})


def test_windows_paths_are_compared_and_test_files_required():
    assert assess_semgrep(report(paths=[r"tests\app.py"]), 0, {"tests/app.py"}) == ["tests/app.py"]
    with pytest.raises(EvidenceError):
        assess_semgrep(report(), 0, {"app.py", "tests/app.py"})


@pytest.mark.parametrize("name", ["../secret", "/secret", r"C:\secret", r"..\secret"])
def test_report_paths_cannot_escape_snapshot(tmp_path, name):
    with pytest.raises(EvidenceError):
        safe_path(tmp_path, name)


def test_sanitized_finding_never_copies_message_or_secret():
    payload = [{"RuleID": "key", "File": "app.py", "StartLine": 2, "EndLine": 2,
                "Secret": "private-fixture-value", "Match": "confidential-source",
                "Description": "untrusted-message"}]
    found = normalize_findings("gitleaks", payload, {"app.py": "a" * 64})
    text = json.dumps(found)
    assert all(secret not in text for secret in ("private-fixture", "confidential", "untrusted"))
    assert found[0]["source_sha256"] == "a" * 64


def test_only_exact_current_finding_can_be_dispositioned():
    finding = normalize_findings("gitleaks", [{"RuleID": "key", "File": "app.py",
                                              "StartLine": 2, "EndLine": 2}], {"app.py": "a" * 64})[0]
    waiver = {"fingerprint": finding["fingerprint"], "reason": "Synthetic fixture; no credential authority.",
              "status": "false_positive", "reviewer": "code-review"}
    assert apply_dispositions([finding], [waiver])[0]["disposition"] == waiver
    changed = {**finding, "fingerprint": "different-source-hash"}
    with pytest.raises(EvidenceError, match="stale_disposition"):
        apply_dispositions([changed], [waiver])
    assert apply_dispositions([changed], [])[0]["disposition"] is None


def test_symlink_source_is_refused(tmp_path):
    external = tmp_path / "outside"
    external.write_text("private")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(external)
    except OSError:
        pytest.skip("Windows symlink permission unavailable")
    with pytest.raises(EvidenceError):
        safe_path(root, "link")


def test_source_path_must_exist(tmp_path):
    with pytest.raises(EvidenceError):
        safe_path(tmp_path, "missing.py")


@pytest.mark.parametrize("code,log", [
    (0, ""), (2, 'TRC scanning path path="app.py"'),
    (0, 'TRC scanning path path="app.py"\nWRN failed to read tests.py'),
])
def test_empty_leak_report_without_actual_coverage_is_not_success(code, log):
    with pytest.raises(EvidenceError):
        assess_gitleaks([], code, log, {"app.py"})


def test_trace_coverage_includes_windows_and_lock_files():
    log = 'TRC scanning path path="tests\\\\app.py"\nTRC scanning path path=package-lock.json'
    assert assess_gitleaks([], 0, log, {"tests/app.py", "package-lock.json"}) == ["package-lock.json", "tests/app.py"]


def test_trace_start_followed_by_binary_skip_is_not_coverage():
    log = 'TRC scanning path path=app.md\nDBG skipping binary file mime_type=application/pdf path=app.md'
    with pytest.raises(EvidenceError, match="skipped_expected_file"):
        assess_gitleaks([], 0, log, {"app.md"})


def test_trace_start_followed_by_archive_skip_with_extra_fields_is_not_coverage():
    log = 'TRC scanning path path="app.zip"\nTRC skipping archive: exceeds max archive depth path="app.zip" max_archive_depth=0'
    with pytest.raises(EvidenceError, match="skipped_expected_file"):
        assess_gitleaks([], 0, log, {"app.zip"})


def test_pattern_or_entropy_filter_is_not_misreported_as_file_skip():
    log = ('TRC scanning path path=app.py\n'
           'TRC skipping finding: low entropy entropy=1.0 path=app.py\n'
           'TRC skipping finding: rule allowlist allowed path=app.py')
    assert assess_gitleaks([], 0, log, {"app.py"}) == ["app.py"]


@pytest.mark.parametrize("body", [None, "<ERROR missing output>", "{}"])
def test_missing_corrupt_or_wrong_report_shape_is_rejected(tmp_path, body):
    path = tmp_path / "report.json"
    if body is not None:
        path.write_text(body)
    with pytest.raises(EvidenceError):
        read_report(path, list)


def test_executable_suffixes_and_worker_dockerfile_are_required(tmp_path, monkeypatch):
    from scripts import security_evidence as module
    names = ["scripts/build.mjs", "backend/Dockerfile.worker", "tests/test_code.py", "image.bin",
             "docs/security/evidence/extra.py"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\x01" if name.endswith(".bin") else b"nonempty")
    monkeypatch.setattr(module, "git", lambda *a: "\0".join(names).encode())
    rows = snapshot(tmp_path, tmp_path / "copy")
    assert {r["path"] for r in rows if r["sast"]} == set(names) - {"image.bin"}
    binary = next(r for r in rows if r["path"] == "image.bin")
    assert binary["exclusion_reason"] == "binary_asset_not_text_scanned"
    worker = next(r for r in rows if r["path"] == "backend/Dockerfile.worker")
    assert worker["sast_alias"].endswith("/Dockerfile")
    assert (tmp_path / "copy" / worker["sast_alias"]).read_bytes() == (tmp_path / worker["path"]).read_bytes()


def test_alias_findings_map_to_original_source_without_waiving_the_file():
    row = {"RuleID": "rule", "File": ".security-aliases/fixture/Dockerfile", "StartLine": 2, "EndLine": 2}
    result = normalize_findings("gitleaks", [row], {"backend/Dockerfile.worker": "a" * 64},
                                {row["File"]: "backend/Dockerfile.worker"})
    assert result[0]["path"] == "backend/Dockerfile.worker"


@pytest.mark.parametrize("scenario,passed", [
    ("ok", True), ("missing", False), ("invalid_json", False), ("wrong_fields", False),
    ("sast_missing", False), ("leaks_missing", False), ("read_warning", False), ("engine_error", False),
])
def test_main_writes_failed_receipt_for_incomplete_scans(tmp_path, monkeypatch, scenario, passed):
    from scripts import security_evidence as module
    root = tmp_path / "repo"
    root.mkdir()
    names = ["app.py", "ops/security/project-rules.yaml", "ops/security/gitleaks.toml"]
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("nonempty")
    rules = tmp_path / "rules.json"
    rules.write_text("rule-cache")
    lock = {"limitations": [], "semgrep": {"version": "1"}, "gitleaks": {"version": "1"},
            "rules": {"sha256": module.digest(rules.read_bytes())}}
    (root / "ops/security/tools.json").write_text(json.dumps(lock))
    (root / "ops/security/dispositions.json").write_text("[]")
    names += ["ops/security/tools.json", "ops/security/dispositions.json"]
    executable = tmp_path / "scanner.exe"
    executable.write_text("fake executable; never launched")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "git", lambda root, *a: b"base-head" if a[0] == "rev-parse" else "\0".join(names).encode())
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="1", returncode=0))
    def execute(command, cwd, env, log):
        log.write_text("")
        if command[1] == "scan":
            output = command[command.index("--output") + 1]
            from pathlib import Path
            if scenario != "missing":
                payload = report(paths=names if scenario != "sast_missing" else ["app.py"])
                if scenario == "wrong_fields":
                    payload["results"] = {}
                Path(output).write_text("invalid" if scenario == "invalid_json" else json.dumps(payload))
            return 2 if scenario == "engine_error" else 0
        from pathlib import Path
        Path(command[command.index("--report-path") + 1]).write_text("[]")
        trace = "\n".join("TRC scanning path path=" + n for n in names)
        log.write_text("" if scenario == "leaks_missing" else trace + ("\nWRN read failed" if scenario == "read_warning" else ""))
        return 0
    monkeypatch.setattr(module, "execute", execute)
    receipt = tmp_path / "receipt.json"
    code = module.main(["--semgrep", str(executable), "--gitleaks", str(executable), "--rules", str(rules),
                        "--work", str(tmp_path / "work"), "--temp", str(tmp_path / "temp"), "--receipt", str(receipt)])
    actual = json.loads(receipt.read_text())
    assert actual["passed"] is passed
    assert actual["complete"] is passed
    assert (code == 0) is passed


@pytest.mark.parametrize("change", ["source", "snapshot", "inventory"])
def test_source_or_snapshot_changes_invalidate_completed_scan(tmp_path, monkeypatch, change):
    from scripts import security_evidence as module
    root, target = tmp_path / "root", tmp_path / "copy"
    root.mkdir()
    target.mkdir()
    for path in (root / "app.py", target / "app.py"):
        path.write_bytes(b"original")
    inventory = [{"path": "app.py", "sha256": module.digest(b"original"), "secret_scan": True}]
    monkeypatch.setattr(module, "git", lambda *a: b"app.py\0extra.py" if change == "inventory" else b"app.py")
    if change != "inventory":
        ((root if change == "source" else target) / "app.py").write_bytes(b"changed")
    with pytest.raises(EvidenceError):
        verify_sources(root, target, inventory)


def test_gitleaks_configuration_retains_rules_without_path_or_empty_waivers():
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "ops/security/gitleaks.toml").read_text(encoding="utf-8"))
    assert len(config["rules"]) == 222
    assert "paths" not in config["allowlist"]
    for rule in config["rules"]:
        for waiver in rule.get("allowlists", []):
            assert "paths" not in waiver
            assert any(waiver.get(key) for key in ("regexes", "stopwords", "commits"))
