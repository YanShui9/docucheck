"""Run the same offline backend gates locally and in CI; never install or call a model."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sanitized_env(environ: Mapping[str, str]) -> dict[str, str]:
    denied = ("DOCUCHECK_", "OPENAI_", "ANTHROPIC_", "LANGSMITH_", "LANGCHAIN_")
    # Only the explicit test font may cross the live project configuration boundary.
    env = {key: value for key, value in environ.items()
           if (key == "DOCUCHECK_CJK_FONT" or not key.startswith(denied))
           and key != "PYTEST_ADDOPTS"}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", LANGSMITH_TRACING="false",
               LANGCHAIN_TRACING_V2="false", PYTHONUTF8="1")
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists():
        print(json.dumps({"error": "report_exists"}))
        return 2
    if sys.version_info[:2] != (3, 12):
        print(json.dumps({"error": "python_312_required"}))
        return 2
    fallback = "C:/Windows/Fonts/simhei.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
    font = Path(os.getenv("DOCUCHECK_CJK_FONT", fallback))
    if not font.is_file():
        print(json.dumps({"error": "cjk_font_missing"}))
        return 2
    env = sanitized_env(os.environ)
    env["DOCUCHECK_CJK_FONT"] = str(font.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    record = {"schema_version": 1, "kind": "offline_backend_verification",
              "started_at_utc": datetime.now(UTC).isoformat(), "python": platform.python_version(),
              "font_sha256": hashlib.sha256(font.read_bytes()).hexdigest(),
              "external_integrations_enabled": False, "network_guard": "scripts.offline_guard",
              "real_model_calls_allowed": False, "checks": []}
    commands = [
        ("dependencies", [sys.executable, "-m", "pip", "check"]),
        ("ruff", [sys.executable, "-m", "ruff", "check", "backend", "evals", "scripts"]),
        ("pytest", [sys.executable, "-m", "pytest", "backend/tests", "-q", "-ra",
                    "-p", "scripts.offline_guard"]),
    ]
    for name, command in commands:
        print(json.dumps({"check": name, "status": "started"}), flush=True)
        started = time.monotonic()
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                    errors="replace", timeout=1200, check=False)
            output, code = result.stdout, result.returncode
        except subprocess.TimeoutExpired:
            output, code = "offline_check_timeout", 1
        row = {"name": name, "command": ["python", *command[1:]], "exit_code": code,
               "seconds": round(time.monotonic() - started, 3), "output": output}
        if name == "pytest":
            for label in ("passed", "skipped", "failed"):
                found = re.search(rf"(\d+) {label}", output)
                row[label] = int(found[1]) if found else 0
            attempts = re.findall(r"^offline_network_attempts=(\d+)$", output, re.MULTILINE)
            row["network_guard_observed"] = bool(attempts)
            row["network_attempts"] = int(attempts[-1]) if attempts else None
            if not attempts or not row["passed"] or row["network_attempts"] != 0:
                row["exit_code"] = code = 1
        record["checks"].append(row)
        print(output, flush=True)
        if code:
            break
    record["passed"] = len(record["checks"]) == len(commands) and all(c["exit_code"] == 0 for c in record["checks"])
    record["finished_at_utc"] = datetime.now(UTC).isoformat()
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": record["passed"], "report": str(args.report)}), flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
