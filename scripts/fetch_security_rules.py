"""Rebuild a pinned private rule cache from immutable Semgrep version IDs (setup only)."""

import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.security_evidence import ROOT, EvidenceError, digest, encoded


def fetch_rule(entry: dict, load) -> dict:
    # URLs come only from the checked-in, reviewed lock, never model/user input.
    url = entry["url"]
    if not url.startswith("https://semgrep.dev/c/") or not url.rsplit("/", 1)[-1].isalnum():
        raise EvidenceError("invalid_rule_version_url")
    with urllib.request.urlopen(url, timeout=30) as response:
        body = response.read(1_000_001)
    if len(body) > 1_000_000:
        raise EvidenceError("rule_too_large")
    rules = load(body)["rules"]
    if len(rules) != 1:
        raise EvidenceError("rule_version_not_unique")
    rule = {**rules[0], "id": entry["id"]}
    if digest(encoded(rule)) != entry["sha256"]:
        raise EvidenceError("rule_version_hash_mismatch")
    return rule


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output_must_not_exist")
    # This helper runs with the isolated Semgrep environment, not the application Python.
    from ruamel.yaml import YAML

    entries = json.loads((ROOT / "ops/security/rule-versions.json").read_text())
    def fetch(entry):
        return fetch_rule(entry, YAML(typ="safe").load)
    with ThreadPoolExecutor(max_workers=6) as pool:
        rules = list(pool.map(fetch, entries))
    content = encoded({"rules": rules})
    lock = json.loads((ROOT / "ops/security/tools.json").read_text())
    if digest(content) != lock["rules"]["sha256"]:
        raise EvidenceError("rule_pack_hash_mismatch")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(content)
    print(json.dumps({"rules": len(rules), "sha256": digest(content)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
