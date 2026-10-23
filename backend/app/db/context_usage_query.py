"""Bounded, read-only projection of the latest request of the active attempt."""

from sqlalchemy import Engine, select

from ..agent.context_usage import ESTIMATE_METHOD
from ..agent.diagnostics import MAX_DIAGNOSTIC_EVENTS, safe_trace_events
from .api_queries import ApiQueryError, _utc
from .models import agent_runs, cases, run_events


def _summary(row) -> dict:
    raw = row["summary_json"]
    if not isinstance(raw, dict):
        return {}
    return safe_trace_events([dict(raw, kind="model")])[0]["summary"]


def _record(row, summary_status):
    summary = _summary(row)
    context = summary.get("context", {})
    if (context.get("estimate_method") != ESTIMATE_METHOD
            or context.get("input_estimate_unit") != "utf8_bytes"
            or context.get("window_fit_method") != "one_byte_per_token_allowance_v1"
            or context.get("exact_tokens") is not False
            or "legacy_input_estimate" not in context
            or "legacy_prompt_limit" not in context
            or context.get("model_window_tokens", 0) <= 0
            or "phase" not in context):
        return None
    memory = summary.get("memory")
    handoff = memory.get("task_handoff_selected") if memory else None
    return {
        "seq": row["seq"], "created_at": _utc(row["created_at"]),
        "phase": context["phase"], "status": summary["status"],
        "provider_called": summary.get("provider_called"),
        "estimate": {
            "input_bytes": context["legacy_input_estimate"],
            "limit_bytes": max(0, context["legacy_prompt_limit"]),
            "model_window_tokens": context["model_window_tokens"],
            "output_reserved_tokens": context.get("output_planned_tokens"),
            "safety_reserved_tokens": context.get("safety_reserved_tokens"),
        },
        "usage": summary.get("usage") or None,
        "memory": ({"history_turns": memory.get("history_turns_selected"),
                    "summary_notes": memory.get("semantic_notes_selected"),
                    "task_handoff": None if handoff is None else bool(handoff)} if memory else None),
        "summary_status": summary_status,
    }


def context_usage_view(engine: Engine, run_id: str) -> dict:
    with engine.connect() as connection:
        run = connection.execute(select(
            agent_runs.c.id, agent_runs.c.case_id, agent_runs.c.conversation_id,
            agent_runs.c.snapshot_id, agent_runs.c.revision, agent_runs.c.active_attempt_id,
            agent_runs.c.status, cases.c.deletion_requested_at,
        ).join(cases, agent_runs.c.case_id == cases.c.id).where(
            agent_runs.c.id == run_id)).mappings().first()
        if run is None:
            raise ApiQueryError("not_found")
        if run["deletion_requested_at"] is not None:
            raise ApiQueryError("case_deleting")
        rows = connection.execute(select(run_events).where(
            run_events.c.run_id == run_id,
            run_events.c.attempt_id == run["active_attempt_id"],
            run_events.c.kind == "agent_model",
        ).order_by(run_events.c.seq.desc()).limit(MAX_DIAGNOSTIC_EVENTS)).mappings().all()
        summary_status = "not_recorded"
        for row in rows:
            summary = _summary(row)
            if summary.get("context", {}).get("phase") == "summary":
                if summary.get("status") in {"completed", "failed"}:
                    summary_status = summary["status"]
                break
        return {
            "run_id": run["id"], "case_id": run["case_id"],
            "conversation_id": run["conversation_id"], "snapshot_id": run["snapshot_id"],
            "revision": run["revision"], "attempt_id": run["active_attempt_id"],
            "run_status": run["status"],
            "record": _record(rows[0], summary_status) if rows else None,
        }
