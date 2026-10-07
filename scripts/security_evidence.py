"""Scan a tracked working-tree snapshot; fail closed and publish only sanitized evidence."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = {".py", ".ts", ".js", ".mjs", ".cjs", ".vue", ".yaml", ".yml", ".json", ".sh", ".ps1"}
GENERATED_RECEIPTS = {
    "docs/security/evidence/r13-final-2026-10-07.json",
    "docs/security/evidence/r13-delivery-2026-10-07.md",
}


class EvidenceError(ValueError):
    """Evidence is incomplete, unsafe, or cannot be bound to the requested source."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def safe_path(root: Path, name: str) -> Path:
    name = name.replace("\\", "/")
    parts = PurePosixPath(name)
    if not name or parts.is_absolute() or PureWindowsPath(name).drive or ".." in parts.parts:
        raise EvidenceError("unsafe_source_path")
    path = root / name
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise EvidenceError("symlink_source")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise EvidenceError("missing_or_external_source")
    return path


def assess_semgrep(report: dict, code: int, expected: set[str]) -> list[str]:
    if code not in (0, 1) or not all(k in report for k in ("results", "errors", "paths", "skipped_rules")):
        raise EvidenceError("semgrep_failed_or_invalid_report")
    if (not all(isinstance(report[k], list) for k in ("results", "errors", "skipped_rules"))
            or not isinstance(report["paths"], dict)
            or not isinstance(report["paths"].get("scanned"), list)
            or not all(isinstance(p, str) for p in report["paths"]["scanned"])):
        raise EvidenceError("semgrep_invalid_field_types")
    if report["errors"] or report["skipped_rules"]:
        raise EvidenceError("semgrep_errors_or_skipped_rules")
    scanned = sorted({p.replace("\\", "/").removeprefix("./") for p in report["paths"].get("scanned", [])})
    if not expected or not expected.issubset(scanned):
        raise EvidenceError("semgrep_missing_source_coverage")
    return scanned


def normalize_findings(tool: str, rows: list, hashes: dict[str, str], aliases: dict[str, str] | None = None) -> list[dict]:
    found = []
    for row in rows:
        if tool == "semgrep":
            rule, path = row["check_id"], row["path"]
            line, end, severity = row["start"]["line"], row["end"]["line"], row["extra"]["severity"]
        else:
            rule, path = row["RuleID"], row["File"]
            line, end, severity = row["StartLine"], row["EndLine"], "secret_candidate"
        path = path.replace("\\", "/").removeprefix("./")
        path = (aliases or {}).get(path, path)
        if path not in hashes:
            raise EvidenceError("finding_outside_inventory")
        record = {"tool": tool, "rule": rule, "path": path, "line": line, "end_line": end,
                  "severity": severity, "source_sha256": hashes[path]}
        record["fingerprint"] = digest(encoded(record))
        found.append(record)
    return sorted({r["fingerprint"]: r for r in found}.values(), key=lambda r: r["fingerprint"])


def assess_gitleaks(report: list, code: int, log: str, expected: set[str]) -> list[str]:
    if code not in (0, 1) or (code == 1) != bool(report):
        raise EvidenceError("gitleaks_failed_or_inconsistent")
    scanned = set()
    for line in log.splitlines():
        if re.search(r"\b(?:WRN|ERR|FTL)\b", line) and not (report and re.search(r"\bWRN leaks found: \d+$", line)):
            raise EvidenceError("gitleaks_read_or_engine_warning")
        match = re.search(r'\bpath=("(?:\\.|[^"\\])*"|\S+)', line)
        name = match[1] if match else None
        if name is not None:
            if name.startswith('"'):
                name = json.loads(name)
            name = name.replace("\\", "/").removeprefix("./")
        if "TRC scanning path" in line and name:
            scanned.add(name)
        skip = re.search(r"\b(?:DBG|TRC|INF) skipping (\w+)", line)
        # Rule-level entropy/pattern filtering is declared policy, not a skipped file.
        if skip and skip[1] != "finding" and name in expected:
            raise EvidenceError("gitleaks_skipped_expected_file")
    if not expected or not expected.issubset(scanned):
        raise EvidenceError("gitleaks_missing_source_coverage")
    return sorted(scanned)


def apply_dispositions(findings: list[dict], dispositions: list[dict]) -> list[dict]:
    current = {f["fingerprint"] for f in findings}
    decisions = {}
    for row in dispositions:
        if row.get("fingerprint") not in current:
            raise EvidenceError("stale_disposition")
        if (row["fingerprint"] in decisions or row.get("status") not in ("false_positive", "accepted_risk")
                or not row.get("reason", "").strip() or not row.get("reviewer", "").strip()):
            raise EvidenceError("invalid_disposition")
        decisions[row["fingerprint"]] = row
    return [{**f, "disposition": decisions.get(f["fingerprint"])} for f in findings]


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True).stdout


def snapshot(root: Path, target: Path) -> list[dict]:
    target.mkdir(parents=True, exist_ok=False)
    inventory = []
    for name in sorted(git(root, "ls-files", "-z").decode().split("\0")):
        if not name:
            continue
        content = safe_path(root, name).read_bytes()
        reason = None
        if name in GENERATED_RECEIPTS:
            reason = "generated_receipt_not_scan_input"
        elif "\x00" in content.decode("utf-8", errors="replace"):
            reason = "binary_asset_not_text_scanned"
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            reason = "non_utf8_asset_not_text_scanned"
        if not reason:
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        sast = not reason and bool(content) and (Path(name).suffix in SOURCE_SUFFIXES or Path(name).name.startswith("Dockerfile"))
        # The downloaded scanner rules are outside the repository snapshot.
        alias = None
        if sast and Path(name).name.startswith("Dockerfile."):
            # CE recognizes the exact Dockerfile basename for Docker semantic rules.
            alias = f".security-aliases/{digest(name.encode())[:16]}/Dockerfile"
            dest = target / alias
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        inventory.append({"path": name, "sha256": digest(content), "bytes": len(content), "sast_alias": alias,
                          "secret_scan": not reason and bool(content), "sast": sast,
                          "exclusion_reason": reason,
                          "sast_reason": None if sast else reason or "no_executable_content_or_no_language_rules"})
    (target / ".semgrepignore").write_text("", encoding="utf-8")
    return inventory


def scanner_env(work: Path, temp: Path) -> dict[str, str]:
    denied = ("DOCUCHECK_", "OPENAI_", "ANTHROPIC_", "LANGSMITH_", "LANGCHAIN_", "SEMGREP_", "GITLEAKS_")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(denied)}
    temp.mkdir(parents=True, exist_ok=True)
    env.update(TEMP=str(temp.resolve()), TMP=str(temp.resolve()), NO_COLOR="1", SEMGREP_SEND_METRICS="off",
               SEMGREP_ENABLE_VERSION_CHECK="0", SEMGREP_SETTINGS_FILE=str(work / "semgrep-settings.yml"),
               PYTHONUTF8="1")
    return env


def verify_sources(root: Path, target: Path, inventory: list[dict]) -> None:
    names = {r["path"] for r in inventory if r["path"] not in GENERATED_RECEIPTS}
    current = {n for n in git(root, "ls-files", "-z").decode().split("\0") if n and n not in GENERATED_RECEIPTS}
    if names != current:
        raise EvidenceError("source_inventory_changed_during_scan")
    for row in inventory:
        if row["path"] in GENERATED_RECEIPTS:
            continue
        if digest(safe_path(root, row["path"]).read_bytes()) != row["sha256"]:
            raise EvidenceError("working_source_changed_during_scan")
        if row["secret_scan"] and digest(safe_path(target, row["path"]).read_bytes()) != row["sha256"]:
            raise EvidenceError("snapshot_changed_during_scan")
        if row.get("sast_alias") and digest(safe_path(target, row["sast_alias"]).read_bytes()) != row["sha256"]:
            raise EvidenceError("snapshot_alias_changed_during_scan")


def execute(command: list[str], cwd: Path, env: dict[str, str], log: Path) -> int:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=600, check=False)
        log.write_bytes(result.stdout)
        return result.returncode
    except subprocess.TimeoutExpired as exc:
        log.write_bytes(exc.stdout or b"")
        return 124


def read_report(path: Path, kind: type):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, kind):
            raise TypeError
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise EvidenceError("missing_or_invalid_json_report") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semgrep", type=Path, required=True)
    parser.add_argument("--gitleaks", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--temp", type=Path, required=True, help="Short local path for Windows socketpair compatibility")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    work = args.work.resolve()
    receipt = args.receipt.resolve()
    if work.exists() or receipt.exists():
        parser.error("work_and_receipt_must_not_exist")
    lock = read_report(ROOT / "ops/security/tools.json", dict)
    record = {"schema_version": 1, "started_at_utc": datetime.now(UTC).isoformat(),
              "source_head": git(ROOT, "rev-parse", "HEAD").decode().strip(),
              "scope": "tracked_current_working_tree_not_git_history", "complete": False, "passed": False,
              "commands": [], "findings": [], "limitations": lock["limitations"]}
    try:
        inventory = snapshot(ROOT, work / "snapshot")
        record["inventory"] = inventory
        # Generated receipts have their own raw-report hashes and must not recursively bind themselves.
        record["source_inventory_sha256"] = digest(encoded([r for r in inventory if r["path"] not in GENERATED_RECEIPTS]))
        hashes = {r["path"]: r["sha256"] for r in inventory}
        expected = {r["path"] for r in inventory if r["sast"]}
        aliases = {r["sast_alias"]: r["path"] for r in inventory if r.get("sast_alias")}
        expected.update(aliases)
        record["sast_aliases"] = aliases
        env = scanner_env(work, args.temp)
        versions = {}
        for name, executable in (("semgrep", args.semgrep), ("gitleaks", args.gitleaks)):
            executable = executable.resolve(strict=True)
            version = subprocess.run([str(executable), "--version" if name == "semgrep" else "version"],
                                     env=env, capture_output=True, text=True, timeout=30, check=True).stdout.strip()
            if version != lock[name]["version"]:
                raise EvidenceError("scanner_version_mismatch")
            versions[name] = {"version": version, "executable_sha256": digest(executable.read_bytes())}
        record["tools"] = versions
        if digest(args.rules.read_bytes()) != lock["rules"]["sha256"]:
            raise EvidenceError("rules_hash_mismatch")
        record["rules"] = lock["rules"]
        record["project_rules_sha256"] = hashes["ops/security/project-rules.yaml"]
        record["gitleaks_config_sha256"] = hashes["ops/security/gitleaks.toml"]
        semgrep_report, leak_report = work / "semgrep.json", work / "gitleaks.json"
        (work / "empty.gitleaksignore").write_text("", encoding="utf-8")
        command = [str(args.semgrep.resolve()), "scan", "--config", str(args.rules.resolve()),
                   "--config", str(work / "snapshot/ops/security/project-rules.yaml"),
                   "--metrics=off", "--disable-version-check", "--no-git-ignore", "--disable-nosem",
                   "--no-rewrite-rule-ids", "--project-root", ".", "--strict", "--jobs=1", "--timeout=20",
                   "--max-target-bytes=0", "--verbose", "--json", "--output", str(semgrep_report)]
        for suffix in sorted(SOURCE_SUFFIXES):
            # Windows native argv globbing can expand a separate '*.yaml' argument into root filenames.
            command.append("--include=*" + suffix)
        command.extend(["--include=Dockerfile*", "."])
        sg_code = execute(command, work / "snapshot", env, work / "semgrep.log")
        record["commands"].append({"tool": "semgrep", "argv": command[1:], "exit_code": sg_code})
        command = [str(args.gitleaks.resolve()), "dir", ".", "--redact=100", "--no-banner",
                   "--config", str(work / "snapshot/ops/security/gitleaks.toml"),
                   "--gitleaks-ignore-path", str(work / "empty.gitleaksignore"), "--log-level=trace",
                   "--ignore-gitleaks-allow", "--report-format=json", "--report-path", str(leak_report), "--exit-code=1"]
        gl_code = execute(command, work / "snapshot", env, work / "gitleaks.log")
        record["commands"].append({"tool": "gitleaks", "argv": command[1:], "exit_code": gl_code})
        record["raw_report_sha256"] = {p.name: digest(p.read_bytes()) for p in (semgrep_report, leak_report) if p.exists()}
        record["raw_log_sha256"] = {p.name: digest(p.read_bytes()) for p in (work / "semgrep.log", work / "gitleaks.log") if p.exists()}
        sg = read_report(semgrep_report, dict)
        leaks = read_report(leak_report, list)
        if sg.get("version") != lock["semgrep"]["version"]:
            raise EvidenceError("semgrep_report_version_mismatch")
        record["semgrep_errors"] = [{"type": e.get("type"), "path": e.get("path"), "code": e.get("code")}
                                    for e in sg.get("errors", []) if isinstance(e, dict)]
        record["semgrep_scanned"] = assess_semgrep(sg, sg_code, expected)
        record["gitleaks_scanned"] = assess_gitleaks(leaks, gl_code, (work / "gitleaks.log").read_text(encoding="utf-8"),
                                                    {r["path"] for r in inventory if r["secret_scan"]})
        findings = normalize_findings("semgrep", sg["results"], hashes, aliases) + normalize_findings("gitleaks", leaks, hashes, aliases)
        record["findings"] = apply_dispositions(findings, read_report(ROOT / "ops/security/dispositions.json", list))
        verify_sources(ROOT, work / "snapshot", inventory)
        record["complete"] = True
        record["passed"] = all(f["disposition"] is not None for f in record["findings"])
    except (EvidenceError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as exc:
        record["failure"] = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
    record["finished_at_utc"] = datetime.now(UTC).isoformat()
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"complete": record["complete"], "passed": record["passed"], "findings": len(record["findings"]),
                      "failure": record.get("failure"), "receipt": str(receipt)}, ensure_ascii=False))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
