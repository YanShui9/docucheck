"""Development tooling must report evidence without broadening execution scope."""

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


def module():
    return importlib.import_module("scripts.dev_workflow")


@pytest.mark.parametrize(("paths", "risk"), [
    (["docs/guide.md"], "low"),
    (["frontend/src/components/Example.vue"], "medium"),
    (["backend/app/db/runs.py"], "high"),
    (["backend/app/new_unknown_module.py"], "high"),
    (["frontend/src/api.ts"], "high"),
    (["backend/tests/test_fixture.py"], "medium"),
    (["docs/guide.md", "backend/migrations/versions/0018.py"], "high"),
    ([".github/workflows/offline-ci.yml"], "high"),
])
def test_risk_is_conservative_for_unknown_code_and_shared_contracts(paths, risk):
    result = module().risk_plan(paths)
    assert result["risk"] == risk
    assert result["advisory_only"] is True
    assert result["ci_full_gate_unchanged"] is True


def test_document_check_handles_local_links_fences_and_missing_paths(tmp_path):
    tool = module()
    (tmp_path / "target.md").write_text("# Target\n", encoding="utf-8")
    source = tmp_path / "entry.md"
    source.write_text("[ok](target.md#target)\n```python\n[fake](missing.md)\n```\n",
                      encoding="utf-8")
    assert tool.check_docs(tmp_path, ["entry.md"])["passed"]
    source.write_text("[bad](missing.md)\n```\n", encoding="utf-8")
    result = tool.check_docs(tmp_path, ["entry.md"])
    assert not result["passed"]
    assert {error["code"] for error in result["errors"]} == {
        "missing_local_link", "unclosed_fence"}


@pytest.mark.parametrize("selection", ["../outside.py", "backend/tests", "--help",
                                         "backend/tests/test_absent.py"])
def test_target_selection_rejects_paths_and_flags_outside_test_files(selection):
    with pytest.raises(ValueError, match="invalid_test_selection"):
        module().select_tests(Path.cwd(), [selection])


def test_target_selection_requires_explicit_tests():
    with pytest.raises(ValueError, match="explicit_tests_required"):
        module().select_tests(Path.cwd(), [])
    assert module().select_tests(Path.cwd(), ["backend/tests/test_dev_workflow.py"]) == [
        "backend/tests/test_dev_workflow.py"]


def test_existing_report_is_preserved_before_any_execution(tmp_path, monkeypatch):
    tool = module()
    report = tmp_path / "result.json"
    report.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(tool, "run_check", lambda *a, **k: pytest.fail("executed"))
    assert tool.main(["check", "--profile", "docs", "--report", str(report)]) == 2
    assert report.read_text(encoding="utf-8") == "preserve"


def test_doctor_does_not_start_services_or_inspect_private_configuration(monkeypatch):
    tool = module()
    result = tool.doctor(Path.cwd())
    assert result["services_checked"] is False
    assert result["model_calls"] == 0
    assert result["configuration_loaded"] is False
    assert ".env" not in json.dumps(result)


def test_process_timeout_and_nonzero_exit_are_not_success(monkeypatch):
    tool = module()
    def timed_out(*a, **k):
        raise subprocess.TimeoutExpired(["python"], 1, output=b"partial")
    monkeypatch.setattr(tool.subprocess, "run", timed_out)
    result = tool.execute([sys.executable, "-V"], Path.cwd(), 1)
    assert result["exit_code"] != 0 and result["timed_out"]
    assert result["output"] == "partial"


def test_targeted_command_sanitizes_live_opt_ins_and_requires_network_observation(monkeypatch):
    tool = module()
    monkeypatch.setenv("DOCUCHECK_RUN_MYSQL_TESTS", "1")
    monkeypatch.setenv("DOCUCHECK_MODEL_API_KEY", "private-fixture")
    captured = {}
    def fake_run(command, **kwargs):
        captured.update(command=command, env=kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "1 passed\n")
    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    result = tool.run_check("targeted", ["backend/tests/test_dev_workflow.py"])
    assert "scripts.offline_guard" in captured["command"]
    assert not any(k.startswith("DOCUCHECK_RUN_") for k in captured["env"])
    assert "DOCUCHECK_MODEL_API_KEY" not in captured["env"]
    assert not result["passed"]  # An exit code without the guard's evidence is insufficient.


def test_targeted_records_scope_and_rejects_caught_network_calls(monkeypatch):
    tool = module()
    monkeypatch.setattr(tool, "execute", lambda *a, **k: {
        "exit_code": 0, "seconds": 0.1,
        "output": "1 passed\noffline_network_attempts=1\n", "timed_out": False})
    result = tool.run_check("targeted", ["backend/tests/test_dev_workflow.py"])
    assert result["coverage"] == ["backend/tests/test_dev_workflow.py"]
    assert not result["passed"] and result["network_attempts"] == 1


def test_real_subprocess_failure_is_captured():
    result = module().execute([sys.executable, "-c", "raise SystemExit(7)"], Path.cwd(), 10)
    assert result["exit_code"] == 7
    assert not result["timed_out"]


def test_full_profile_reuses_original_gate_and_disallows_test_selection(monkeypatch, tmp_path):
    tool = module()
    captured = {}
    def fake_execute(command, cwd, timeout):
        captured.update(command=command, cwd=cwd, timeout=timeout)
        Path(command[-1]).write_text(json.dumps({"passed": True, "checks": [
            {"name": "dependencies", "exit_code": 0}, {"name": "ruff", "exit_code": 0},
            {"name": "pytest", "exit_code": 0, "passed": 1, "network_attempts": 0}]}),
            encoding="utf-8")
        return {"exit_code": 0, "seconds": 1, "timed_out": False,
                "output": "1 passed\noffline_network_attempts=0\n"}
    monkeypatch.setattr(tool, "execute", fake_execute)
    assert tool.run_check("full", [], tmp_path / "full.json")["passed"]
    assert "scripts.verify_offline" in captured["command"]
    assert captured["cwd"] == tool.ROOT
    # Three bounded 1200-second checks plus time for the runner's report handling.
    assert captured["timeout"] == 3660
    with pytest.raises(ValueError, match="tests_only_for_targeted"):
        tool.run_check("full", ["backend/tests/test_dev_workflow.py"])


def test_changed_source_cannot_be_reported_as_passing(tmp_path, monkeypatch):
    tool = module()
    identities = iter([{"commit": "before", "worktree_sha256": "same"},
                       {"commit": "after", "worktree_sha256": "same"}])
    monkeypatch.setattr(tool, "source_identity", lambda: next(identities))
    monkeypatch.setattr(tool, "run_check", lambda *a, **k: {"passed": True})
    report = tmp_path / "changed.json"
    assert tool.main(["check", "--profile", "docs", "--report", str(report)]) == 1
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["source_changed_during_check"] and not result["passed"]


def test_execution_error_leaves_a_failed_structured_report(tmp_path, monkeypatch):
    tool = module()
    monkeypatch.setattr(tool, "source_identity", lambda: {"commit": "fixture"})
    def failed(*a, **k):
        raise ValueError("fixture_failure")
    monkeypatch.setattr(tool, "run_check", failed)
    report = tmp_path / "error.json"
    assert tool.main(["check", "--profile", "docs", "--report", str(report)]) == 1
    result = json.loads(report.read_text(encoding="utf-8"))
    assert not result["passed"] and result["error"] == "fixture_failure"


def test_verification_identity_changes_when_selected_tests_change(tmp_path):
    tool = module()
    folder = tmp_path / "backend/tests"
    folder.mkdir(parents=True)
    path = folder / "test_fixture.py"
    path.write_text("def test_one(): assert True\n", encoding="utf-8")
    first = tool.verification_identity("targeted", ["backend/tests/test_fixture.py"], tmp_path)
    path.write_text("def test_two(): assert True\n", encoding="utf-8")
    assert tool.verification_identity("targeted", ["backend/tests/test_fixture.py"], tmp_path) != first


def test_full_profile_rejects_missing_or_failed_backend_evidence(monkeypatch, tmp_path):
    tool = module()
    monkeypatch.setattr(tool, "execute", lambda *a, **k: {
        "exit_code": 0, "seconds": 1, "timed_out": False,
        "output": "1 passed\noffline_network_attempts=0\n"})
    assert not tool.run_check("full", [], tmp_path / "missing.json")["passed"]
    report = tmp_path / "failed.json"
    def fake_execute(*a, **k):
        report.write_text(json.dumps({"passed": False, "checks": []}), encoding="utf-8")
        return {"exit_code": 0, "seconds": 1, "timed_out": False,
                "output": "1 passed\noffline_network_attempts=0\n"}
    monkeypatch.setattr(tool, "execute", fake_execute)
    assert not tool.run_check("full", [], report)["passed"]
