"""Server-owned context and typed graph state."""
from dataclasses import dataclass
from typing import TypedDict

from langchain_core.messages import BaseMessage

from ..rules.fields import RULE_VERSION as FIELDS_RULE_VERSION
from ..rules.memory import RULE_VERSION as MEMORY_RULE_VERSION


@dataclass(frozen=True)
class RunContext:
    case_id: str
    snapshot_id: str
    revision: int
    conversation_id: str
    run_id: str
    attempt_id: str
    model_id: str | None
    mode: str = "standard"
    prompt_version: str = "agent-m2-v2"
    rule_version: str = f"{FIELDS_RULE_VERSION}+{MEMORY_RULE_VERSION}"


class AgentState(TypedDict):
    messages: list[BaseMessage]
    status: str
    answer: str
    error: str | None
    repairs: int
    tool_repairs: int
    next_node: str


@dataclass(frozen=True)
class RunResult:
    context: RunContext
    status: str
    answer: str
    error: str | None
    usage: dict
    events: tuple[dict, ...]
