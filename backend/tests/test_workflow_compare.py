"""Comparisons must not turn different workloads or failures into efficiency claims."""

import copy
import importlib

import pytest


def module():
    return importlib.import_module("scripts.workflow_compare")


def record(seconds=10):
    return {"schema_version": 1, "kind": "development_workflow_check", "passed": True,
            "profile": "targeted", "coverage": ["backend/tests/test_fixture.py"],
            "seconds": seconds, "verification_sha256": "same-test-corpus",
            "source_changed_during_check": False, "network_guard": "scripts.offline_guard",
            "network_attempts": 0,
            "identity": {"python": "3.12.14", "platform": "fixture-platform",
                         "dependencies_sha256": "same-dependencies", "commit": "fixture",
                         "worktree_sha256": "fixture", "dirty": False}}


def test_compatible_comparison_is_an_observation_not_a_proven_gain():
    before, after = record(10), record(8)
    after["identity"]["commit"] = "candidate"
    result = module().compare(before, after)
    assert result["seconds_saved_observed"] == 2
    assert result["relative_reduction_pct_observed"] == 20
    assert result["causal_improvement_established"] is False
    assert result["trials_per_side"] == 1


@pytest.mark.parametrize(("key", "value"), [
    ("passed", False), ("profile", "full"), ("coverage", ["backend/tests/test_other.py"]),
    ("verification_sha256", "different-tests"), ("source_changed_during_check", True),
    ("network_attempts", 1), ("seconds", float("nan")),
    ("seconds", float("inf")), ("seconds", -1), ("seconds", True),
])
def test_comparison_rejects_failed_or_different_workloads(key, value):
    before, after = record(), record()
    after[key] = value
    with pytest.raises(ValueError):
        module().compare(before, after)


@pytest.mark.parametrize("key", ["platform", "python", "dependencies_sha256"])
def test_comparison_requires_same_observed_environment(key):
    before, after = record(), record()
    after["identity"][key] = "different"
    with pytest.raises(ValueError, match="incompatible_environment"):
        module().compare(before, after)


def test_comparison_requires_complete_identity_and_zero_baseline_is_not_divided():
    before, after = record(0), record(1)
    assert module().compare(before, after)["relative_reduction_pct_observed"] is None
    damaged = copy.deepcopy(after)
    del damaged["identity"]["commit"]
    with pytest.raises(ValueError, match="invalid_identity"):
        module().compare(before, damaged)


@pytest.mark.parametrize("damaged", [[], {}, {"profile": "unknown"}])
def test_malformed_reports_are_rejected_with_a_controlled_error(damaged):
    with pytest.raises(ValueError):
        module().compare(record(), damaged)
