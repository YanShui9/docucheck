"""Measure assembled requests using the legacy estimate, before budget rejection.

These are diagnostics, not a tokenizer or the provider's wire format. Message
contents and tool arguments are represented only by a request fingerprint.
"""

import json

from langchain_core.messages import BaseMessage

from ..evidence.repository import digest
from .policy import Budget, RunStopped, input_bound

CONTEXT_PHASES = frozenset({"routing", "edit", "evidence", "decide", "extract", "summary"})
ESTIMATE_METHOD = "serialized_utf8_plus_512_v1"


def measure_request(messages: list[BaseMessage], schemas: list[dict], *,
                    budget: Budget, output_cap: int, phase: str) -> dict:
    if phase not in CONTEXT_PHASES:
        raise ValueError("invalid_context_phase")
    planned_output = budget.planned_output(output_cap)
    return {
        "version": 1,
        "phase": phase,
        "estimate_method": ESTIMATE_METHOD,
        "exact_tokens": False,
        "model_window_tokens": budget.context_window,
        "legacy_input_estimate": input_bound(messages, schemas),
        "legacy_schema_bytes": len(json.dumps(schemas, ensure_ascii=False).encode("utf-8")),
        "legacy_prompt_limit": budget.prompt_limit(output_cap),
        "legacy_task_input_remaining": (None if budget.max_input is None else
                                        max(0, budget.max_input - budget.input_reserved)),
        "input_estimate_unit": "utf8_bytes",
        "window_fit_method": "one_byte_per_token_allowance_v1",
        "safety_reserved_tokens": budget.safety_reserved,
        "task_input_hard_limit_tokens": budget.max_input,
        "task_output_hard_limit_tokens": budget.max_output,
        "output_requested_tokens": output_cap,
        "output_planned_tokens": max(0, planned_output),
        "request_digest": digest([
            [message.model_dump(mode="json") for message in messages], schemas,
        ]),
    }


def _request_event(budget: Budget, messages: list[BaseMessage], schemas: list[dict], *,
                   output_cap: int, phase: str, sequence: int) -> dict:
    return {
        "kind": "model", "operation": "extract" if phase in {"extract", "summary"} else "decide",
        "sequence": sequence, "status": "rejected", "provider_called": False,
        "context": measure_request(messages, schemas, budget=budget,
                                   output_cap=output_cap, phase=phase),
    }


def reject_context_request(budget: Budget, messages: list[BaseMessage], schemas: list[dict], *,
                           output_cap: int, phase: str, events: list[dict],
                           error: str = "context_budget") -> None:
    """Record a fitter's existing denial without reserving or changing counters."""
    event = _request_event(budget, messages, schemas, output_cap=output_cap,
                           phase=phase, sequence=len(events) + 1)
    event["error"] = error
    events.append(event)
    raise RunStopped(error)


def reserve_with_context(budget: Budget, messages: list[BaseMessage], schemas: list[dict], *,
                         output_cap: int, phase: str, events: list[dict],
                         structured_repair: bool = False, semantic_repair: bool = False,
                         history_turns_dropped: int = 0) -> tuple[dict, int, dict]:
    event = _request_event(budget, messages, schemas, output_cap=output_cap,
                           phase=phase, sequence=len(events) + 1)
    # Attach repair metadata before reserve: reserve itself can reject the call.
    if structured_repair:
        event["structured_repair"] = True
    if semantic_repair:
        event["semantic_repair"] = True
    if history_turns_dropped:
        event["history_turns_dropped"] = history_turns_dropped
    events.append(event)
    try:
        options, size = budget.reserve(messages, schemas, output_cap=output_cap)
    except RunStopped as exc:
        event["error"] = exc.code
        raise
    event["status"] = "started"
    return options, size, event
