"""Only the authorized Run's bounded numeric usage is public."""

from sqlalchemy import update

from backend.app.db.models import run_attempts
from backend.tests.test_m4_workbench_api import _fixture


def test_run_usage_is_visible_only_to_its_owner(tmp_path, monkeypatch):
    engine, client, alice, bob, case_id, first, _ = _fixture(tmp_path, monkeypatch)
    conv = client.post(f"/api/v1/cases/{case_id}/conversations", headers=alice).json()
    receipt = client.post(f"/api/v1/conversations/{conv['id']}/messages",
                          headers={**alice, "Idempotency-Key": "usage"},
                          json={"content": "查询内存", "mode": "thorough",
                                "expected_revision": first["revision"]}).json()
    path = f"/api/v1/agent-runs/{receipt['run_id']}"
    pending = client.get(path, headers=alice).json()
    assert pending.get("usage") is None
    assert pending.get("model_id") is None
    with engine.begin() as connection:
        connection.execute(update(run_attempts).where(
            run_attempts.c.id == receipt["attempt_id"]).values(
                model_id="fixture-model", usage_json={"model_calls": 2, "actual_input": 301,
                    "actual_output": 29, "unknown_usage_calls": 1,
                    "api_key": "secret-" + "test-sentinel", "base_url": "private",
                    "actual_input_bad": True, "tools": -1}))
    observed = client.get(path, headers=alice)
    assert observed.status_code == 200
    assert observed.json()["model_id"] == "fixture-model"
    assert observed.json()["usage"] == {"model_calls": 2, "actual_input": 301,
                                        "actual_output": 29, "unknown_usage_calls": 1}
    assert "secret-test-sentinel" not in observed.text
    assert client.get(path, headers=bob).status_code == 404
    engine.dispose()
