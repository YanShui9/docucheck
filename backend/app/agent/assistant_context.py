"""Compact routing metadata and trim optional memories before fixed procurement state."""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from .context import MEMORY_NOTICE, _memory_payload, fit_decision_history
from .policy import input_bound


def compact_schema(schema):
    """Remove display titles only; keep every validation and discrimination keyword."""
    if isinstance(schema, list):
        return [compact_schema(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key == "title" and isinstance(value, str):
            continue
        # Property/definition names and literal values are data, not annotations.
        if key in {"properties", "$defs", "patternProperties", "dependentSchemas"}:
            result[key] = {name: compact_schema(child) for name, child in value.items()}
        elif key in {"const", "enum", "default", "examples"}:
            result[key] = value
        else:
            result[key] = compact_schema(value)
    return result


def assemble_assistant_context(guidance, question, history, layers, schemas, budget, *, tail=(), history_available=False):
    payload, already_omitted = _memory_payload(layers)
    total_entries = len(payload["summary"]["entries"]) + already_omitted
    recent = history[-8:]
    limit = min(budget.max_prompt, budget.context_window - 1536)
    while True:
        notice = MEMORY_NOTICE if history_available else MEMORY_NOTICE.replace("需要完整旧内容时按 message_id 调用 read_conversation_history；", "")
        system = guidance + notice + "\nMEMORY_DATA=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        messages = [SystemMessage(content=system), *(m for turn in recent for m in turn), HumanMessage(content=question), *tail]
        fitted, dropped = fit_decision_history(messages, schemas, max_prompt=budget.max_prompt,
                                               context_window=budget.context_window, output_cap=1536)
        size = input_bound(fitted, schemas)
        if size <= limit:
            break
        # fit_decision_history returns oversized mandatory context; it does not raise.
        if payload["summary"]["entries"]:
            payload["summary"]["entries"].pop(0)
        elif payload["summary"]["id"] is not None:
            payload["summary"]["id"] = None
        elif payload["preferences"]:
            payload["preferences"] = {}
        else:
            break  # Budget.reserve will reject, without clipping fixed rules or the question.
    return fitted, {
        "input_bound": size,
        "prompt_limit": max(0, limit),
        "mandatory_overflow": size > limit,
        "history_turns_selected": len(recent) - dropped,
        "history_turns_omitted": len(history) - len(recent) + dropped,
        "summary_entries_selected": len(payload["summary"]["entries"]),
        "summary_entries_omitted": total_entries - len(payload["summary"]["entries"]),
        "preferences_selected": len(payload["preferences"]),
    }
