"""Conservative comparisons of development checks, never a causal performance claim."""

import math


def compare(baseline: dict, candidate: dict) -> dict:
    for row in (baseline, candidate):
        if (not isinstance(row, dict) or row.get("schema_version") != 1
                or row.get("profile") not in ("docs", "targeted", "full", "frontend")
                or row.get("kind") != "development_workflow_check"
                or row.get("passed") is not True or row.get("source_changed_during_check") is not False
                or not row.get("coverage") or not row.get("verification_sha256")):
            raise ValueError("invalid_or_failed_check")
        seconds = row.get("seconds")
        if (type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds < 0):
            raise ValueError("invalid_duration")
        identity = row.get("identity")
        if not isinstance(identity, dict) or not all(identity.get(key) for key in (
                "commit", "worktree_sha256", "python", "platform", "dependencies_sha256")):
            raise ValueError("invalid_identity")
        if row.get("profile") in ("targeted", "full") and (
                row.get("network_guard") != "scripts.offline_guard" or row.get("network_attempts") != 0):
            raise ValueError("invalid_offline_evidence")
    for key in ("profile", "coverage", "verification_sha256", "network_guard"):
        if baseline.get(key) != candidate.get(key):
            raise ValueError("incompatible_verification_scope")
    for key in ("python", "platform", "dependencies_sha256"):
        if baseline["identity"][key] != candidate["identity"][key]:
            raise ValueError("incompatible_environment")
    before, after = baseline["seconds"], candidate["seconds"]
    return {"kind": "development_check_observation", "profile": baseline["profile"],
            "baseline_identity": baseline["identity"], "candidate_identity": candidate["identity"],
            "seconds_saved_observed": round(before - after, 3),
            "relative_reduction_pct_observed": round((before - after) / before * 100, 2)
            if before else None, "trials_per_side": 1, "causal_improvement_established": False,
            "limitations": ["single_observation", "cache_and_machine_load_not_controlled",
                            "not_business_latency_or_model_token_savings",
                            "not_a_report_signature_or_independent_quality_evaluation"]}
