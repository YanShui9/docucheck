"""Exercise the real HTTP routes; parser/model doubles stay explicit and local."""

import json
import shutil
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from backend.app.api.main import create_app
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.models import metadata
from backend.app.db.run_executor import execute_run
from backend.app.parsing import parse_mineru_middle, verify_pdf_source_coverage
from backend.tests.test_m4_workbench_api import DemoAuthenticator
from evals.m2_agent import ScriptedProvider

EXAMPLES = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def setup(tmp_path, monkeypatch, *, execute=True):
    inputs = tmp_path / "input-only"
    inputs.mkdir()
    docs = []
    for name, role in (("requirements.pdf", "requirement"),
                       ("vendor-a-quote.xlsx", "quote"), ("vendor-a-spec.pdf", "spec")):
        source = inputs / name
        shutil.copyfile(EXAMPLES / name, source)
        docs.append({"filename": name, "role": role,
                     "supplier": None if role == "requirement" else "vendor-a",
                     "model": None if role == "requirement" else "Model-X1",
                     "sha256": sha256(source.read_bytes()).hexdigest(),
                     "bytes": source.stat().st_size, "pages": int(name.endswith(".pdf"))})
    (inputs / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "pack_id": "transport-test", "dataset_kind": "synthetic_candidate",
        "suppliers": [{"id": "vendor-a", "model": "Model-X1"}], "documents": docs,
        "question": "请完整审查全部供应商的八项指标，并列出来源",
    }, ensure_ascii=False), encoding="utf-8")
    engine = create_engine(f"sqlite:///{tmp_path / 'transport.db'}")
    metadata.create_all(engine)
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(tmp_path / "storage"))
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()),
                        headers={"Authorization": "Bearer alice"})
    project = client.post("/api/v1/projects", json={"name": "Eval fixture"}).json()

    def pdf(path, document_id):
        cache = ("requirements.middle.json" if path.read_bytes() ==
                 (EXAMPLES / "requirements.pdf").read_bytes() else "vendor-a-spec.middle.json")
        return verify_pdf_source_coverage(parse_mineru_middle(
            EXAMPLES / "parsed" / cache, document_id=document_id), path)

    ingestion = DocumentIngestion(engine, tmp_path / "storage", pdf_parser=pdf)
    submitted, requests = [], []

    class WorkerClient:
        def request(self, method, path, **kwargs):
            requests.append((method, path, kwargs))
            if execute and method == "GET" and path.endswith("/documents"):
                current = client.get(path).json()
                ingestion.process_snapshot(current["case_id"], current["snapshot_id"])
            if execute and method == "GET" and path.startswith("/api/v1/agent-runs/") and submitted:
                receipt = submitted.pop()
                execute_run(engine, receipt["run_id"], receipt["attempt_id"],
                            ScriptedProvider(("vendor-a",)))
            response = client.request(method, path, **kwargs)
            if method == "POST" and path.endswith("/messages") and response.status_code == 202:
                submitted.append(response.json())
            return response

    return engine, WorkerClient(), inputs, project["id"], requests, client


def test_inputs_only_target_uploads_originals_once_and_reads_formal_export(tmp_path, monkeypatch):
    from evals.m5_run import run_pack

    engine, client, inputs, project, requests, _ = setup(tmp_path, monkeypatch)
    clock = Clock()
    result = run_pack(client, inputs, project_id=project, clock=clock.now, sleep=clock.sleep)
    assert result["stage"] == "finished", result
    assert result["run"]["status"] == "completed"
    assert result["run"]["model_id"] == "scripted-development"
    assert result["run"]["usage"]["model_calls"] > 0
    assert len(result["export"]["result"]["checks"]) == 8
    uploads = [kwargs for method, path, kwargs in requests if method == "POST" and path.endswith("/documents")]
    assert len(uploads) == 3
    assert [item["data"]["expected_revision"] for item in uploads] == ["0", "1", "2"]
    assert all("gold" not in item["data"] for item in uploads)
    messages = [kwargs for method, path, kwargs in requests if method == "POST" and path.endswith("/messages")]
    assert len(messages) == 1
    assert set(messages[0]["json"]) == {"content", "mode", "expected_revision"}
    assert result["application_cache_reused"] is False
    assert result["model_authenticity"] == "not_verified_by_runner"
    engine.dispose()


def test_timeout_cancels_the_new_run_without_retry_or_shrinking_the_task(tmp_path, monkeypatch):
    from evals.m5_run import run_pack

    engine, client, inputs, project, requests, raw_client = setup(tmp_path, monkeypatch, execute=False)
    # No worker means parsing times out before a model Run is submitted.
    clock = Clock()
    failed = run_pack(client, inputs, project_id=project, deadline_seconds=2,
                      clock=clock.now, sleep=clock.sleep)
    assert failed["error_code"] == "parse_timeout"
    assert failed["case_id"]
    assert not any(path.endswith("/messages") for _, path, _ in requests)

    # Parse succeeds, but the isolated worker deliberately never starts the Agent.
    for name in ("requirements.pdf", "vendor-a-spec.pdf"):
        assert (inputs / name).is_file()
    class ParsedClient:
        def request(self, method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if method == "GET" and path.endswith("/documents"):
                payload = response.json()
                for document in payload["documents"]:
                    document["parse_status"] = "complete"
                import httpx
                return httpx.Response(200, json=payload)
            return response
    second = run_pack(ParsedClient(), inputs, project_id=project, deadline_seconds=2,
                      clock=clock.now, sleep=clock.sleep)
    assert second["error_code"] == "run_timeout"
    assert second["case_id"] != failed["case_id"]
    assert second["cancel_requested"] is True
    assert raw_client.get(f"/api/v1/agent-runs/{second['run_id']}").json()["status"] == "cancelled"
    assert not any(path.endswith(("/retry", "/resume")) for _, path, _ in requests)
    engine.dispose()


@pytest.mark.parametrize("url", ["https://example.org", "http://127.0.0.1@evil.test:8000",
                                "http://127.0.0.1:8000/private", "http://127.0.0.1:8000?token=x"])
def test_live_target_rejects_non_loopback_or_embedded_credentials(url):
    from evals.m5_run import validate_api_url

    with pytest.raises(ValueError):
        validate_api_url(url)


def test_live_preview_does_not_load_token_or_open_a_client(tmp_path, monkeypatch, capsys):
    from evals import m5_run

    monkeypatch.setattr(m5_run, "prepare_protocol", lambda *a, **k: {
        "planned_tasks": 1, "maximum_agent_requests": 32})
    monkeypatch.setattr(m5_run.httpx, "Client", lambda *a, **k: pytest.fail("preview_network"))
    monkeypatch.setattr(m5_run, "os", SimpleNamespace(environ=SimpleNamespace(
        get=lambda *a, **k: pytest.fail("preview_credentials"))))
    monkeypatch.setattr("sys.argv", ["m5_run", "--output", str(tmp_path / "preview")])
    assert m5_run.main() == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "preview"


def test_performance_failure_and_reused_case_remain_in_ten_attempts(monkeypatch):
    from backend.tests.test_m5_metrics import example
    from evals import m5_run

    manifest, gold, observation = example()
    protocol = {"kind": "performance", "tasks": [
        {"task_id": f"attempt-{n}", "pack_id": "fixture", "gold_sha256": "fixed",
         "input": manifest.model_dump()} for n in range(10)]}
    monkeypatch.setattr(m5_run, "_bytes", lambda path: gold.model_dump_json().encode())
    class Digest:
        def hexdigest(self):
            return "fixed"
    monkeypatch.setattr(m5_run, "sha256", lambda content: Digest())
    monkeypatch.setattr(m5_run, "runtime_manifest", lambda path: manifest.model_dump())
    monkeypatch.setattr(m5_run, "original_texts", lambda path: {})
    monkeypatch.setattr(m5_run, "score_pack", lambda *a, **k: {
        "status_accuracy": {"numerator": 8, "denominator": 8},
        "necessary_evidence": {"numerator": 8, "denominator": 8}, "dangerous_meets": 0,
        "citation_errors": {"numerator": 0, "denominator": 16},
        "task_success": a[2].get("stage") == "finished", "confusion_matrix": {}})
    observations = {f"attempt-{n}": {**observation, "case_id": f"case-{n}",
                                     "upload_to_result_ms": 119_999} for n in range(9)}
    report = m5_run.score_observations(Path("unused"), protocol, observations)
    assert report["performance"]["numerator"] == 9
    assert report["performance"]["denominator"] == 10
    assert report["performance"]["threshold_met"] is True
    observations["attempt-0"]["case_id"] = "case-1"
    report = m5_run.score_observations(Path("unused"), protocol, observations)
    assert report["performance"]["numerator"] == 7
    assert report["performance"]["threshold_met"] is False


def test_api_setup_failure_still_writes_all_planned_failures(tmp_path, monkeypatch, capsys):
    from evals import m5_run

    protocol = {"kind": "quality", "planned_tasks": 2, "maximum_agent_requests": 64,
                "tasks": [{"task_id": "first", "pack_id": "fixture"},
                          {"task_id": "second", "pack_id": "fixture"}]}
    monkeypatch.setattr(m5_run, "prepare_protocol", lambda *a, **k: protocol)
    monkeypatch.setenv("DOCUCHECK_EVAL_TOKEN", "test-token-must-not-appear")
    class DownClient:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def request(self, *args, **kwargs):
            import httpx
            raise httpx.ConnectError("test-token-must-not-appear")
    monkeypatch.setattr(m5_run.httpx, "Client", lambda *a, **k: DownClient())
    def score_failures(root, plan, observations):
        assert set(observations) == {"first", "second"}
        assert all(o["stage"] == "not_started" for o in observations.values())
        return {"agent_tasks": {"numerator": 0, "denominator": 2}}
    monkeypatch.setattr(m5_run, "score_observations", score_failures)
    output = tmp_path / "failed"
    monkeypatch.setattr("sys.argv", ["m5_run", "--send", "--output", str(output)])
    assert m5_run.main() == 1
    assert json.loads((output / "report.json").read_text(encoding="utf-8"))["agent_tasks"]["denominator"] == 2
    assert "test-token-must-not-appear" not in capsys.readouterr().out


def test_changed_manifest_is_refused_before_contacting_the_target(tmp_path, monkeypatch):
    from evals.candidate_corpus import InputManifest, runtime_manifest
    from evals.m5_run import run_pack

    engine, _, inputs, project, _, _ = setup(tmp_path, monkeypatch)
    expected = InputManifest.model_validate(runtime_manifest(inputs))
    changed = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    changed["question"] = "A different question after planning"
    (inputs / "manifest.json").write_text(json.dumps(changed), encoding="utf-8")
    class NoRequests:
        def request(self, *args, **kwargs):
            pytest.fail("changed_input_sent")
    report = run_pack(NoRequests(), inputs, project_id=project, expected_manifest=expected)
    assert report["error_code"] == "evaluation_input_changed"
    engine.dispose()


@pytest.mark.parametrize("fault", ["missing_attempt", "invalid_attempt", "mismatched_terminal"])
def test_corrupt_receipt_or_wrong_terminal_run_cancels_the_accepted_run(tmp_path, monkeypatch, fault):
    import httpx

    from evals.m5_run import run_pack

    engine, client, inputs, project, requests, api = setup(tmp_path, monkeypatch, execute=False)

    class CorruptResponseClient:
        def request(self, method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            payload = response.json()
            if method == "GET" and path.endswith("/documents"):
                for document in payload["documents"]:
                    document["parse_status"] = "complete"
            elif method == "POST" and path.endswith("/messages"):
                if fault == "missing_attempt":
                    payload.pop("attempt_id")
                elif fault == "invalid_attempt":
                    payload["attempt_id"] = "invalid"
            elif method == "GET" and path.startswith("/api/v1/agent-runs/"):
                payload.update(id="f" * 32, status="completed")
            return httpx.Response(response.status_code, json=payload)

    clock = Clock()
    result = run_pack(CorruptResponseClient(), inputs, project_id=project,
                      clock=clock.now, sleep=clock.sleep)

    assert result["error_code"] is not None
    assert result["cancel_requested"] is True
    assert api.get(f"/api/v1/agent-runs/{result['run_id']}").json()["status"] == "cancelled"
    cancels = [path for method, path, _ in requests if method == "POST" and path.endswith("/cancel")]
    assert cancels == [f"/api/v1/agent-runs/{result['run_id']}/cancel"]
    assert not any(path.endswith(("/retry", "/resume")) for _, path, _ in requests)
    engine.dispose()
