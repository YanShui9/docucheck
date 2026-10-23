"""Read-only context view: scope, latest attempt and truthful measurement units."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, func, select, update

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.context_usage import reserve_with_context
from backend.app.agent.diagnostics import safe_trace_events
from backend.app.agent.policy import Budget, RunStopped
from backend.app.api.main import create_app
from backend.app.db.models import cases, metadata, run_events
from backend.app.evidence.repository import EvidenceRepository
from backend.app.runtime.lifecycle import RunStatus
from backend.tests import test_run_repository
from backend.tests.test_api_workspace_authorization import DemoAuthenticator
from backend.tests.test_assistant_agent import Decisions

repository = test_run_repository.repository


def event(*, size=1200, phase="routing", **extra):
    return {"kind": "model", "operation": "decide", "status": "completed",
            "provider_called": True, "context": {
                "version": 1, "phase": phase, "estimate_method": "serialized_utf8_plus_512_v1",
                "exact_tokens": False, "input_estimate_unit": "utf8_bytes",
                "window_fit_method": "one_byte_per_token_allowance_v1",
                "legacy_input_estimate": size, "legacy_prompt_limit": 9000,
                "model_window_tokens": 10000, "output_planned_tokens": 500,
                "safety_reserved_tokens": 500,
            }, **extra}


def finish(repository, events):
    receipt = test_run_repository._submit(repository)
    claim = repository.claim(receipt.run_id, receipt.attempt_id)
    repository.finish(receipt.run_id, receipt.attempt_id, claim.fence, claim.snapshot_id,
                      RunStatus.PARTIAL, trace_events=events)
    return receipt


def get(repository, run_id):
    with TestClient(create_app(repository.engine)) as client:
        response = client.get(f"/api/v1/agent-runs/{run_id}/context-usage")
    assert response.status_code == 200, response.text
    return response.json()


def test_latest_request_has_explicit_units_and_only_allowed_public_values(repository):
    receipt = finish(repository, [event(size=8000), event(size=1200,
        usage={"input_tokens": 250, "output_tokens": 30, "total_tokens": 280},
        memory={"history_turns_selected": 3, "semantic_notes_selected": 2,
                "task_handoff_selected": 1, "private": "SECRET"}, prompt="SECRET")])
    with repository.engine.connect() as connection:
        before = connection.scalar(select(func.count()).select_from(run_events))
    view = get(repository, receipt.run_id)
    assert (view["run_id"], view["case_id"], view["conversation_id"], view["attempt_id"]) == (
        receipt.run_id, "case-a", "conv-a", receipt.attempt_id)
    assert view["snapshot_id"] == "snapshot-a" and view["revision"] == 1
    record = view["record"]
    assert record["estimate"] == {"input_bytes": 1200, "limit_bytes": 9000,
        "model_window_tokens": 10000, "output_reserved_tokens": 500, "safety_reserved_tokens": 500}
    assert record["usage"] == {"input_tokens": 250, "output_tokens": 30, "total_tokens": 280}
    assert record["memory"] == {"history_turns": 3, "summary_notes": 2, "task_handoff": True}
    assert record["summary_status"] == "not_recorded"
    assert "SECRET" not in str(view) and "request_digest" not in str(view)
    get(repository, receipt.run_id)
    with repository.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(run_events)) == before


def test_reservation_records_memory_before_local_rejection_and_projects_safely():
    events = []
    with pytest.raises(RunStopped, match="context_budget"):
        reserve_with_context(Budget(max_prompt=1), [], [], output_cap=512, phase="routing",
            events=events, memory_audit={"history_turns_selected": 2, "semantic_notes_selected": 1,
                "task_handoff_selected": 0, "text": "SECRET"})
    safe = safe_trace_events(events)[0]["summary"]
    assert safe["memory"] == {"history_turns_selected": 2, "semantic_notes_selected": 1,
                              "task_handoff_selected": 0}
    assert safe["provider_called"] is False and "SECRET" not in str(safe)


@pytest.mark.parametrize("bad", [True, -1, 1.5, "2", 2**40, None])
def test_memory_projection_does_not_coerce_untrusted_counts(bad):
    raw = event(memory={"history_turns_selected": bad, "semantic_notes_selected": 0,
                        "task_handoff_selected": 2})
    safe = safe_trace_events([raw])[0]["summary"]
    assert safe["memory"] == {"semantic_notes_selected": 0}


def test_summary_completed_and_selected_summary_are_distinct(repository):
    receipt = finish(repository, [event(phase="summary"), event(memory={
        "history_turns_selected": 0, "semantic_notes_selected": 0, "task_handoff_selected": 0})])
    record = get(repository, receipt.run_id)["record"]
    assert record["summary_status"] == "completed"
    assert record["memory"]["summary_notes"] == 0


def test_assistant_event_uses_selected_memory_of_the_actual_request():
    history = [(HumanMessage(content="旧问题"), AIMessage(content="旧回答")) for _ in range(2)]
    agent = AssistantAgent(EvidenceRepository.empty("case-a", "snapshot-a"),
        Decisions(("chat", {"answer": "请继续说明"})), history=history,
        memory_layers={"semantic_summary": {"notes": [{"kind": "goal", "text": "历史目标",
            "source_message_ids": ["old-message"]}]}}, history_reader=lambda *_args, **_kw: [])
    result = agent.ask("接着讨论")
    model = next(row for row in safe_trace_events(result["events"]) if row["kind"] == "agent_model")
    assert model["summary"]["memory"] == {"history_turns_selected": 2,
        "semantic_notes_selected": 1, "task_handoff_selected": 0}
    assert "历史目标" not in str(model)


def test_unknown_usage_and_unavailable_memory_stay_unknown(repository):
    receipt = finish(repository, [event(status="rejected", provider_called=False)])
    record = get(repository, receipt.run_id)["record"]
    assert record["usage"] is None and record["memory"] is None
    assert record["provider_called"] is False and record["status"] == "rejected"


def test_legacy_last_event_does_not_reuse_earlier_request_measurements(repository):
    receipt = finish(repository, [event(), {"kind": "model", "operation": "decide", "status": "failed"}])
    assert get(repository, receipt.run_id)["record"] is None


def test_retry_does_not_display_previous_attempt_context(repository):
    receipt = finish(repository, [event()])
    repository.retry(receipt.run_id, "snapshot-a", 1, idempotency_key="view-retry")
    view = get(repository, receipt.run_id)
    assert view["attempt_id"] != receipt.attempt_id
    assert view["record"] is None


def test_last_request_is_found_after_more_than_a_page_of_events(repository):
    receipt = finish(repository, [event(size=1000 + i) for i in range(127)])
    assert get(repository, receipt.run_id)["record"]["estimate"]["input_bytes"] == 1126


@pytest.mark.parametrize("field,value", [("exact_tokens", True), ("legacy_input_estimate", True),
    ("input_estimate_unit", "tokens"), ("model_window_tokens", -1)])
def test_malformed_estimate_is_unrecorded_instead_of_fabricated(repository, field, value):
    raw = event()
    raw["context"][field] = value
    receipt = finish(repository, [raw])
    assert get(repository, receipt.run_id)["record"] is None


def test_negative_limit_is_a_zero_allowance_not_a_negative_capacity(repository):
    raw = event(size=1200)
    raw["context"]["legacy_prompt_limit"] = -500
    receipt = finish(repository, [raw])
    assert get(repository, receipt.run_id)["record"]["estimate"]["limit_bytes"] == 0


def test_deleted_case_and_unknown_run_cannot_be_read(repository):
    receipt = finish(repository, [event()])
    with repository.engine.begin() as connection:
        connection.execute(update(cases).where(cases.c.id == "case-a").values(
            deletion_requested_at=datetime.now(UTC).replace(tzinfo=None)))
    with TestClient(create_app(repository.engine)) as client:
        assert client.get(f"/api/v1/agent-runs/{receipt.run_id}/context-usage").status_code == 409
        assert client.get("/api/v1/agent-runs/missing/context-usage").status_code == 404


def test_http_context_view_requires_membership_and_returns_no_prompt(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'view-auth.db'}")
    metadata.create_all(engine)
    try:
        with TestClient(create_app(engine, authenticator=DemoAuthenticator())) as client:
            alice = {"Authorization": "Bearer alice"}
            project = client.post("/api/v1/projects", headers=alice, json={"name": "采购"}).json()
            case = client.post("/api/v1/cases", headers=alice, json={"project_id": project["id"]}).json()["id"]
            conv = client.post(f"/api/v1/cases/{case}/conversations", headers=alice).json()["id"]
            receipt = client.post(f"/api/v1/conversations/{conv}/messages",
                headers=alice | {"Idempotency-Key": "view-auth"},
                json={"content": "SECRET", "operation": "assistant", "expected_revision": 0}).json()
            url = f"/api/v1/agent-runs/{receipt['run_id']}/context-usage"
            assert client.get(url).status_code == 401
            assert client.get(url, headers={"Authorization": "Bearer bob"}).status_code == 404
            response = client.get(url, headers=alice)
            assert response.status_code == 200 and response.json()["record"] is None
            assert "SECRET" not in response.text
    finally:
        engine.dispose()
