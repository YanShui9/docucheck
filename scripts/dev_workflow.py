"""Local development evidence; never start services, load credentials or install packages."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from scripts.verify_offline import sanitized_env

ROOT = Path(__file__).resolve().parents[1]
ENTRY_DOCS = ["AGENTS.md", "CLAUDE.md", "README.md", "SECURITY.md", "docs/README.md",
              "docs/development-roadmap.md", "docs/step-by-step-plan.md",
              "docs/development-workflow.md"]


def git(*args: str, root: Path = ROOT) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)


def source_identity(root: Path = ROOT) -> dict:
    changes = git("diff", "--binary", "HEAD", "--", root=root)
    untracked = git("ls-files", "--others", "--exclude-standard", "-z", root=root)
    digest = hashlib.sha256(changes)
    for raw in untracked.split(b"\0"):
        if raw:
            path = root / os.fsdecode(raw)
            digest.update(raw)
            digest.update(os.fsencode(os.readlink(path)) if path.is_symlink()
                          else path.read_bytes() if path.is_file() else b"not_a_file")
    packages = sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
                      if d.metadata["Name"])
    return {"commit": git("rev-parse", "HEAD", root=root).decode().strip(),
            "dirty": bool(changes or untracked), "worktree_sha256": digest.hexdigest(),
            "python": platform.python_version(), "platform": platform.platform(),
            "dependencies_sha256": hashlib.sha256(json.dumps(packages).encode()).hexdigest()}


def doctor(root: Path = ROOT) -> dict:
    return {"kind": "local_development_inventory", "identity": source_identity(root),
            "python_312": sys.version_info[:2] == (3, 12),
            "npm_available": bool(shutil.which("npm.cmd" if os.name == "nt" else "npm")),
            "lock_files_present": all((root / p).is_file() for p in (
                "backend/requirements-dev.lock.txt", "frontend/package-lock.json")),
            "services_checked": False, "configuration_loaded": False, "model_calls": 0,
            "limitations": ["not_service_readiness", "not_database_migration_verification",
                            "not_business_e2e"]}


def verification_identity(profile: str, tests: list[str], root: Path = ROOT) -> str:
    """Fingerprint the selected assertions/fixtures, separate from application changes."""
    files = set()
    if profile == "targeted":
        files.update(root / name for name in select_tests(root, tests))
        files.update(root / name for name in ("conftest.py", "backend/conftest.py",
                                              "backend/tests/conftest.py"))
        files.update((root / "backend/tests").glob("*helpers.py"))
    elif profile == "full":
        for folder in ("backend/tests", "examples", "evals"):
            files.update((root / folder).rglob("*"))
    elif profile == "frontend":
        files.update((root / "frontend/src").rglob("*.test.*"))
        files.update((root / "frontend/tests").rglob("*"))
        files.update(root / name for name in ("frontend/package.json", "frontend/package-lock.json",
                                              "frontend/vite.config.ts", "frontend/tsconfig.json"))
    else:
        return hashlib.sha256(json.dumps(ENTRY_DOCS).encode()).hexdigest()
    files.update(root / name for name in ("scripts/offline_guard.py", "scripts/verify_offline.py"))
    digest = hashlib.sha256()
    for path in sorted(files):
        if (path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
                and path.suffix not in (".pyc", ".pyo")):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def changed_paths(base: str, root: Path = ROOT) -> list[str]:
    ref = git("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}", root=root)
    tracked = git("diff", "--name-only", "-z", ref.decode().strip(), "--", root=root)
    untracked = git("ls-files", "--others", "--exclude-standard", "-z", root=root)
    return sorted({os.fsdecode(p).replace("\\", "/")
                   for p in (tracked + untracked).split(b"\0") if p})


def risk_plan(paths: list[str]) -> dict:
    ranks = []
    for path in paths:
        if path.endswith(".md") or (path.startswith("docs/assets/")
                                    and path.endswith((".png", ".svg", ".jpg"))):
            ranks.append(0)
        elif (path.startswith(("backend/tests/", "frontend/tests/")) or path in (
                "scripts/dev_workflow.py", "scripts/workflow_compare.py")
                or (path.startswith("frontend/src/") and not any(part in path.lower()
                    for part in ("api", "auth", "config")))):
            ranks.append(1)
        else:
            ranks.append(2)
    rank = max(ranks, default=0)
    return {"risk": ("low", "medium", "high")[rank], "changed_paths": paths,
            "recommended_checks": ["docs"] + (["targeted", "full"] if rank else [])
            + (["frontend"] if any(p.startswith("frontend/") for p in paths) else [])
            + (["affected_isolated_integrations"] if rank == 2 else []),
            "advisory_only": True, "ci_full_gate_unchanged": True,
            "requires_business_assertions": rank > 0,
            "integration_commands_are_not_executed": True}


def check_docs(root: Path, files: list[str]) -> dict:
    errors, count, sizes = [], 0, {}
    for name in files:
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            errors.append({"file": name, "code": "missing_document"})
            continue
        data = path.read_bytes()
        text = data.decode("utf-8")
        sizes[name] = len(text.replace("\r\n", "\n").encode("utf-8"))
        if "\ufffd" in text:
            errors.append({"file": name, "code": "replacement_character"})
        fence = ""
        for line in text.splitlines():
            match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
            if match:
                marker = match[1]
                if not fence:
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence):
                    fence = ""
                continue
            if fence:
                continue
            for match in re.finditer(r"\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", line):
                url = match[1].strip("<>")
                parsed = urlsplit(url)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                target = (path.parent / unquote(parsed.path)).resolve()
                count += 1
                if not target.is_relative_to(root.resolve()) or not target.exists():
                    errors.append({"file": name, "code": "missing_local_link", "target": url})
        if fence:
            errors.append({"file": name, "code": "unclosed_fence"})
    return {"passed": not errors, "errors": errors, "links_checked": count,
            "document_bytes_lf": sizes, "scope": files,
            "limitations": ["anchors_and_remote_urls_not_checked", "not_semantic_doc_review"]}


def select_tests(root: Path, tests: list[str]) -> list[str]:
    if not tests:
        raise ValueError("explicit_tests_required")
    result = []
    for name in tests:
        path = Path(name)
        resolved = (root / path).resolve()
        allowed = (root / "backend/tests").resolve()
        if (path.is_absolute() or not resolved.is_relative_to(allowed)
                or not resolved.is_file() or not re.fullmatch(r"test_[\w]+\.py", path.name)):
            raise ValueError("invalid_test_selection")
        result.append(resolved.relative_to(root.resolve()).as_posix())
    return sorted(set(result))


def execute(command: list[str], cwd: Path, timeout: int) -> dict:
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=cwd, env=sanitized_env(os.environ),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout, check=False)
        code, output, timed_out = result.returncode, result.stdout, False
    except subprocess.TimeoutExpired as exc:
        code, output, timed_out = 1, exc.output or "", True
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
    return {"exit_code": code, "seconds": round(time.monotonic() - started, 3),
            "output": output, "timed_out": timed_out}


def run_check(profile: str, tests: list[str], backend_report: Path | None = None) -> dict:
    if profile != "targeted" and tests:
        raise ValueError("tests_only_for_targeted")
    if profile == "docs":
        return {"profile": profile, "coverage": ENTRY_DOCS, **check_docs(ROOT, ENTRY_DOCS)}
    if profile == "targeted":
        coverage = select_tests(ROOT, tests)
        commands = [[sys.executable, "-m", "pytest", *coverage, "-q", "-ra",
                     "-p", "scripts.offline_guard"]]
        cwd, timeout = ROOT, 600
    elif profile == "full":
        if backend_report is None or backend_report.exists():
            raise ValueError("fresh_backend_report_required")
        coverage = ["backend/tests", "ruff:backend,evals,scripts", "pip:check"]
        commands = [[sys.executable, "-m", "scripts.verify_offline", "--report", str(backend_report)]]
        cwd, timeout = ROOT, 660
    else:
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise ValueError("npm_not_available")
        coverage, cwd, timeout = ["frontend:test", "frontend:build"], ROOT / "frontend", 600
        commands = [[npm, "test"], [npm, "run", "build"]]
    rows = []
    for command in commands:
        print(json.dumps({"check": profile, "status": "started"}), flush=True)
        row = {"command": command, **execute(command, cwd, timeout)}
        rows.append(row)
        if row["exit_code"]:
            break
    observed = re.findall(r"^offline_network_attempts=(\d+)$",
                          "\n".join(r["output"] for r in rows), re.MULTILINE)
    guarded = profile in ("targeted", "full")
    passed = len(rows) == len(commands) and all(r["exit_code"] == 0 for r in rows)
    if guarded:
        passed = passed and bool(observed) and all(int(n) == 0 for n in observed)
        if profile == "targeted":
            passed = passed and bool(re.search(r"\b[1-9]\d* passed\b", rows[0]["output"]))
        else:
            try:
                backend = json.loads(backend_report.read_text(encoding="utf-8"))
                checks = {c["name"]: c for c in backend["checks"]}
                passed = (passed and backend.get("passed") is True
                          and all(checks[name]["exit_code"] == 0
                                  for name in ("dependencies", "ruff", "pytest"))
                          and checks["pytest"]["passed"] > 0
                          and checks["pytest"]["network_attempts"] == 0)
            except (OSError, ValueError, KeyError, TypeError):
                passed = False
    return {"profile": profile, "coverage": coverage, "passed": bool(passed), "checks": rows,
            "network_guard": "scripts.offline_guard" if guarded else "not_instrumented",
            "network_attempts": sum(map(int, observed)) if observed else None,
            "limitations": ["not_real_service_or_model_validation"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor")
    plan = sub.add_parser("plan")
    plan.add_argument("--base", default="HEAD")
    compare = sub.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    check = sub.add_parser("check")
    check.add_argument("--profile", choices=("docs", "targeted", "full", "frontend"), required=True)
    check.add_argument("--tests", nargs="+", default=[])
    check.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "doctor":
            result = doctor()
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["python_312"] and result["lock_files_present"] else 1
        if args.action == "plan":
            print(json.dumps(risk_plan(changed_paths(args.base)), ensure_ascii=False))
            return 0
        if args.action == "compare":
            from scripts.workflow_compare import compare
            result = compare(json.loads(args.baseline.read_text(encoding="utf-8")),
                             json.loads(args.candidate.read_text(encoding="utf-8")))
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.report.exists():
            raise ValueError("report_exists")
        args.report = args.report.resolve()
        if sys.version_info[:2] != (3, 12):
            raise ValueError("python_312_required")
        if args.profile == "targeted":
            select_tests(ROOT, args.tests)
        elif args.tests:
            raise ValueError("tests_only_for_targeted")
        backend_report = args.report.with_suffix(".backend.json")
        if args.profile == "full" and backend_report.exists():
            raise ValueError("fresh_backend_report_required")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as stream:
            started = time.monotonic()
            record = {"schema_version": 1, "kind": "development_workflow_check",
                      "started_at_utc": datetime.now(UTC).isoformat(), "identity": source_identity(),
                      "verification_sha256": verification_identity(args.profile, args.tests)}
            try:
                record.update(run_check(args.profile, args.tests, backend_report))
                after = source_identity()
                record["source_changed_during_check"] = (
                    after["commit"] != record["identity"]["commit"] or
                    after["worktree_sha256"] != record["identity"]["worktree_sha256"])
                record["passed"] = record["passed"] and not record["source_changed_during_check"]
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                record.update(passed=False, profile=args.profile,
                              error=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
            record["seconds"] = round(time.monotonic() - started, 3)
            record["finished_at_utc"] = datetime.now(UTC).isoformat()
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({"passed": record["passed"], "report": str(args.report)}, ensure_ascii=False))
        return 0 if record["passed"] else 1
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(json.dumps({"error": message}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
