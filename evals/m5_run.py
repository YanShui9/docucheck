"""Preview or explicitly execute version-bound development evaluations via API.

No .env is loaded, no production services are started, and no gold is sent to
the target. --send may cause the server's configured parser/model to run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from backend.app.agent.policy import MODE_LIMITS

from .candidate_corpus import (
    CorpusIndex,
    InputManifest,
    ProposedGold,
    _safe,
    runtime_manifest,
    validate_corpus,
)
from .corpus_review import _bytes, _code_baseline, _encoded
from .m5_metrics import aggregate_scores, original_texts, score_pack

DEFAULT_REVIEW = Path(__file__).parent / "acceptance/development_review_2026_10_02.json"
TERMINAL = {"completed", "failed", "partial", "cancelled", "interrupted", "waiting_user"}


class EvaluationError(ValueError):
    """A safe code; never retain server bodies, authorization or client exceptions."""


def validate_api_url(value: str) -> str:
    try:
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or url.hostname not in {"127.0.0.1", "::1"}
                or url.username or url.password or url.query or url.fragment
                or url.path not in {"", "/"} or not 1 <= (url.port or 80) <= 65535):
            raise ValueError
    except ValueError:
        raise ValueError("evaluation_api_must_be_literal_loopback") from None
    return value.rstrip("/")


def _identifier(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}|[a-f0-9]{64}", value):
        raise EvaluationError("api_identifier_invalid")
    return value


def _request(client, method: str, path: str, *, expected: int = 200, **kwargs) -> dict:
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError:
        raise EvaluationError("http_transport_error") from None
    if response.status_code != expected:
        # Only a bounded status is logged. External error details may contain secrets.
        raise EvaluationError(f"http_status_{response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise EvaluationError("api_json_invalid") from None
    if not isinstance(payload, dict):
        raise EvaluationError("api_json_invalid")
    return payload


def development_baseline(root: Path, review_path: Path) -> dict:
    assets = validate_corpus(root)
    if assets["errors"]:
        raise ValueError("evaluation_corpus_invalid")
    index = CorpusIndex.model_validate_json(_bytes(_safe(root, "corpus.json")))
    review = json.loads(_bytes(review_path))
    if (not isinstance(review, dict)
            or review.get("kind") != "ai_source_review_of_agent_authored_candidates"
            or review.get("independent_human_review") is not False
            or review.get("formal_independent_acceptance") is not False
            or review.get("known_author_exposure") is not True
            or review.get("independently_sealed_packs") != 0
            or review.get("corpus_sha256") != sha256(_bytes(_safe(root, "corpus.json"))).hexdigest()
            or review.get("packs") != [p.model_dump() for p in index.packs]):
        raise ValueError("development_review_version_or_provenance")
    expected = {}
    for pack in index.packs:
        gold = ProposedGold.model_validate_json(_bytes(_safe(root, f"gold/{pack.id}.json")))
        for row in gold.results:
            expected[(pack.id, row.supplier, str(row.field_key))] = row.business_status
    observed = {}
    rows = review.get("reviewed_rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("development_review_coverage")
    for row in rows:
        key = (row["pack"], row["supplier"], row["field"])
        if key in observed or row.get("decision") != "accepted_for_development" or not row.get("source_reason"):
            raise ValueError("development_review_coverage")
        observed[key] = row["reviewed_status"]
    if observed != expected:
        raise ValueError("development_review_coverage")
    return {"scope": "ai_reviewed_development_corpus", "corpus_sha256": review["corpus_sha256"],
            "review_sha256": sha256(_bytes(review_path)).hexdigest(),
            "reviewed_rows": len(observed), "independently_sealed_packs": 0,
            "formal_independent_acceptance": False}


def prepare_protocol(root: Path, review_path: Path, *, packs: list[str] | None = None,
                     performance: bool = False) -> dict:
    baseline = development_baseline(root, review_path)
    index = CorpusIndex.model_validate_json(_bytes(_safe(root, "corpus.json")))
    available = {p.id: p for p in index.packs}
    selected = [index.performance_pack] if performance else (packs or list(available))
    if len(set(selected)) != len(selected) or set(selected) - set(available) or (performance and packs):
        raise ValueError("evaluation_selection_invalid")
    tasks = []
    for pack in selected:
        manifest = InputManifest.model_validate(runtime_manifest(_safe(root, f"inputs/{pack}")))
        if performance and len(manifest.documents) != 5:
            raise ValueError("performance_requires_fixed_five_files")
        for attempt in range(10 if performance else 1):
            tasks.append({"task_id": f"{pack}-{attempt + 1:02d}", "pack_id": pack,
                          "partition": available[pack].partition, "input": manifest.model_dump(),
                          "gold_sha256": available[pack].gold_sha256})
    limits = MODE_LIMITS["thorough"]
    return {"schema_version": 1, "kind": "performance" if performance else "quality",
            "created_at": datetime.now(UTC).isoformat(), "baseline": baseline,
            "code_baseline": _code_baseline(), "server_code_identity": "not_verified",
            "parser_identity": "not_verified", "mode": "thorough", "tasks": tasks,
            "planned_tasks": len(tasks), "application_cache_reuse_allowed": False,
            "new_case_and_conversation_per_attempt": True, "automatic_retries": False,
            "maximum_agent_requests": len(tasks) * limits["max_calls"],
            "maximum_input_tokens": len(tasks) * limits["max_input"],
            "maximum_output_tokens": len(tasks) * limits["max_output"],
            "price_and_cost": "unknown_until_verified_prices_and_complete_usage",
            "model_authenticity": "not_verified_by_runner", "actual_business_measured": False}


def run_pack(client, pack_dir: Path, *, project_id: str, deadline_seconds: float = 240,
             expected_manifest: InputManifest | None = None,
             clock=time.monotonic, sleep=time.sleep) -> dict:
    """Target boundary: only an input-only directory and API, never gold/review."""
    manifest = InputManifest.model_validate(runtime_manifest(pack_dir))
    observation = {"pack_id": manifest.pack_id, "stage": "create_case", "error_code": None,
                   "application_cache_reused": False, "model_authenticity": "not_verified_by_runner",
                   "cancel_requested": False, "export": None}
    if expected_manifest is not None and manifest != expected_manifest:
        observation.update(stage="input_validation", error_code="evaluation_input_changed")
        return observation
    started = clock()
    deadline = started + deadline_seconds
    upload_started = parse_done = agent_started = None
    running = False

    def check_time(stage):
        if clock() >= deadline:
            raise EvaluationError(f"{stage}_timeout")

    try:
        project_id = _identifier(project_id)
        case = _request(client, "POST", "/api/v1/cases", expected=201,
                        json={"project_id": project_id})
        case_id = observation["case_id"] = _identifier(case["id"])
        revision = case["revision"]
        if revision != 0:
            raise EvaluationError("evaluation_requires_empty_case")
        observation["stage"] = "upload"
        upload_started = clock()
        for document in manifest.documents:
            check_time("upload")
            content = _safe(pack_dir, document.filename).read_bytes()
            if len(content) != document.bytes or sha256(content).hexdigest() != document.sha256:
                raise EvaluationError("evaluation_input_changed")
            data = {"role": document.role, "expected_revision": str(revision)}
            if document.supplier is not None:
                data.update(supplier_handle=document.supplier, supplier_name=document.supplier,
                            model_name=document.model)
            media_type = ("application/pdf" if document.filename.endswith(".pdf") else
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            receipt = _request(client, "POST", f"/api/v1/cases/{case_id}/documents", expected=201,
                               headers={"Idempotency-Key": uuid4().hex}, data=data,
                               files={"file": (document.filename, content, media_type)})
            if receipt["source_sha256"] != document.sha256 or receipt["revision"] != revision + 1:
                raise EvaluationError("upload_receipt_mismatch")
            revision = receipt["revision"]
            observation["snapshot_id"] = _identifier(receipt["snapshot_id"])
        snapshot = observation["snapshot_id"]
        observation["revision"] = revision
        observation["stage"] = "parse"
        while True:
            check_time("parse")
            listed = _request(client, "GET", f"/api/v1/cases/{case_id}/snapshots/{snapshot}/documents")
            rows = observation["documents"] = listed["documents"]
            bindings = {(d["original_name"], d["source_sha256"], d["role"],
                         d["supplier_handle"], d.get("model_name")) for d in rows}
            expected = {(d.filename, d.sha256, d.role, d.supplier, d.model) for d in manifest.documents}
            if (listed["snapshot_id"] != snapshot or listed["revision"] != revision
                    or bindings != expected or len(rows) != len(expected)):
                raise EvaluationError("snapshot_document_binding")
            states = {d["parse_status"] for d in rows}
            if "failed" in states:
                raise EvaluationError("parser_failed")
            if states <= {"complete", "partial"}:
                break
            sleep(min(0.5, max(0, deadline - clock())))
        parse_done = clock()
        conversation = _request(client, "POST", f"/api/v1/cases/{case_id}/conversations", expected=201)
        conversation_id = observation["conversation_id"] = _identifier(conversation["id"])
        check_time("run")
        observation["stage"] = "run"
        agent_started = clock()
        receipt = _request(client, "POST", f"/api/v1/conversations/{conversation_id}/messages", expected=202,
                           headers={"Idempotency-Key": uuid4().hex},
                           json={"content": manifest.question, "mode": "thorough", "expected_revision": revision})
        run_id = observation["run_id"] = _identifier(receipt["run_id"])
        running = True
        observation["attempt_id"] = _identifier(receipt["attempt_id"])
        while True:
            check_time("run")
            run = observation["run"] = _request(client, "GET", f"/api/v1/agent-runs/{run_id}")
            if (run["id"] != run_id or run["case_id"] != case_id or run["snapshot_id"] != snapshot
                    or run["active_attempt_id"] != observation["attempt_id"] or run["revision"] != revision):
                raise EvaluationError("run_receipt_mismatch")
            if run["status"] in TERMINAL:
                running = False
                break
            sleep(min(0.5, max(0, deadline - clock())))
        observation["error_code"] = run.get("error_code")
        observation["stage"] = "export"
        if run["status"] in {"completed", "partial", "waiting_user"}:
            try:
                observation["export"] = _request(client, "GET", f"/api/v1/agent-runs/{run_id}/export?format=json")
            except EvaluationError as exc:
                observation["export_error"] = str(exc)
        observation["stage"] = "finished"
    except (EvaluationError, KeyError, TypeError, ValueError, OSError) as exc:
        observation["error_code"] = str(exc) if isinstance(exc, EvaluationError) else "invalid_input_or_api_shape"
    except KeyboardInterrupt:
        observation["error_code"] = "client_interrupted"
    finally:
        if running and observation.get("run_id"):
            try:
                _request(client, "POST", f"/api/v1/agent-runs/{observation['run_id']}/cancel")
                observation["cancel_requested"] = True
            except EvaluationError as exc:
                observation["cancellation_error"] = str(exc)
        end = clock()
        observation["elapsed_ms"] = round((end - started) * 1000, 2)
        observation["upload_to_result_ms"] = round((end - upload_started) * 1000, 2) if upload_started is not None else None
        observation["upload_and_parse_ms"] = round((parse_done - upload_started) * 1000, 2) if parse_done is not None else None
        observation["agent_and_export_ms"] = round((end - agent_started) * 1000, 2) if agent_started is not None else None
    return observation


def _write_new(path: Path, value: dict) -> None:
    with path.open("xb") as stream:
        stream.write(_encoded(value))


def score_observations(root: Path, protocol: dict, observations: dict[str, dict]) -> dict:
    scores = []
    for task in protocol["tasks"]:
        pack_id = task["pack_id"]
        observation = observations.get(task["task_id"], {"pack_id": pack_id, "stage": "not_started"})
        gold_path = _safe(root, f"gold/{pack_id}.json")
        if sha256(_bytes(gold_path)).hexdigest() != task["gold_sha256"]:
            raise ValueError("evaluation_gold_changed")
        pack_dir = _safe(root, f"inputs/{pack_id}")
        manifest = InputManifest.model_validate(runtime_manifest(pack_dir))
        if manifest.model_dump() != task["input"]:
            raise ValueError("evaluation_inputs_changed")
        score = score_pack(manifest, ProposedGold.model_validate_json(_bytes(gold_path)),
                           observation, source_texts=original_texts(pack_dir))
        score["task_id"] = task["task_id"]
        scores.append(score)
    report = aggregate_scores(scores)
    report["protocol_kind"] = protocol["kind"]
    report["usage_and_latency"] = [
        {"task_id": t["task_id"], "model_id": observations.get(t["task_id"], {}).get("run", {}).get("model_id"),
         "usage": observations.get(t["task_id"], {}).get("run", {}).get("usage"),
         "upload_to_result_ms": observations.get(t["task_id"], {}).get("upload_to_result_ms"),
         "cost": None, "cost_reason": "verified_price_and_cache_usage_not_available"} for t in protocol["tasks"]]
    if protocol["kind"] == "performance":
        case_counts = Counter(o.get("case_id") for o in observations.values() if o.get("case_id"))
        fast = sum(s["task_success"] and observations.get(s["task_id"], {}).get("upload_to_result_ms") is not None
                   and observations[s["task_id"]]["upload_to_result_ms"] <= 120_000
                   and case_counts.get(observations[s["task_id"]].get("case_id")) == 1
                   for s in scores)
        report["performance"] = {"numerator": fast, "denominator": len(scores), "required_successes": 9,
                                 "seconds_limit": 120, "threshold_met": len(scores) == 10 and fast >= 9,
                                 "formal_independent_acceptance": False}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/m5-candidates"))
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--pack", action="append")
    parser.add_argument("--performance", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id")
    parser.add_argument("--token-env", default="DOCUCHECK_EVAL_TOKEN")
    parser.add_argument("--deadline-seconds", type=int, default=240)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if not 1 <= args.deadline_seconds <= 600:
            raise ValueError("evaluation_deadline_invalid")
        protocol = prepare_protocol(args.root, args.review, packs=args.pack, performance=args.performance)
        output = args.output or Path("data/m5-runs") / (datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
        output.mkdir(parents=True, exist_ok=False)
        _write_new(output / "protocol.json", protocol)
        if not args.send:
            print(json.dumps({"mode": "preview", "planned_tasks": protocol["planned_tasks"],
                              "maximum_agent_requests": protocol["maximum_agent_requests"],
                              "formal_independent_acceptance": False, "path": str(output)}, ensure_ascii=False))
            return 0
        url = validate_api_url(args.api_url)
        token = os.environ.get(args.token_env)
        if not token:
            raise ValueError("evaluation_oidc_token_missing")
        observations = {}
        with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"},
                          timeout=15, follow_redirects=False, trust_env=False) as client:
            stopped = "not_started"
            try:
                project = args.project_id or _request(client, "POST", "/api/v1/projects", expected=201,
                                                      json={"name": "M5 development evaluation"})["id"]
                for task in protocol["tasks"]:
                    observation = run_pack(client, _safe(args.root, f"inputs/{task['pack_id']}"),
                                           project_id=project, deadline_seconds=args.deadline_seconds,
                                           expected_manifest=InputManifest.model_validate(task["input"]))
                    observations[task["task_id"]] = observation
                    _write_new(output / f"{task['task_id']}.json", observation)
                    print(json.dumps({"task": task["task_id"], "stage": observation["stage"],
                                      "error_code": observation["error_code"]}, ensure_ascii=False), flush=True)
                    if observation["error_code"] == "client_interrupted":
                        stopped = "client_interrupted"
                        break
            except EvaluationError as exc:
                stopped = str(exc)
            except (ValueError, OSError, TypeError, KeyError):
                stopped = "invalid_input_or_api_shape"
            except KeyboardInterrupt:
                stopped = "client_interrupted"
            for task in protocol["tasks"]:
                if task["task_id"] not in observations:
                    observation = {"pack_id": task["pack_id"], "stage": "not_started", "error_code": stopped}
                    observations[task["task_id"]] = observation
                    _write_new(output / f"{task['task_id']}.json", observation)
        report = score_observations(args.root, protocol, observations)
        _write_new(output / "report.json", report)
        print(json.dumps({"mode": "send", "path": str(output), "agent_tasks": report["agent_tasks"],
                          "formal_independent_acceptance": False}, ensure_ascii=False))
        return int(report["agent_tasks"]["numerator"] != report["agent_tasks"]["denominator"])
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"error": "evaluation_setup_or_version_invalid", "formal_independent_acceptance": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
