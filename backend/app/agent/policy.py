"""One budget shared by decisions, extraction and tool execution."""
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage

MODE_LIMITS = {
    "standard": {"max_decisions": 8, "max_tools": 16, "max_calls": 16,
                 "max_input": 48000, "max_output": 6000, "max_prompt": 16000},
    "thorough": {"max_decisions": 12, "max_tools": 24, "max_calls": 32,
                 "max_input": 96000, "max_output": 12000, "max_prompt": 24000},
}


class RunStopped(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def input_bound(messages: list[BaseMessage], schemas: list[dict]) -> int:
    # Byte-count upper estimate for the supported byte-tokenizer adapter.
    # Usage returned by the provider is recorded separately. Include serialization
    # and message/tool overhead, not just document text.
    body = [{"role": m.type, "content": m.content,
             "tool_calls": getattr(m, "tool_calls", [])} for m in messages]
    return len(json.dumps([body, schemas], ensure_ascii=False).encode("utf-8")) + 512


@dataclass
class Budget:
    max_decisions: int = 8
    context_window: int = 32768
    cancelled: Callable[[], bool] = lambda: False
    max_input: int = 48000
    max_output: int = 6000
    max_prompt: int = 16000
    max_calls: int = 16
    max_tools: int = 16
    seconds: float = 120
    decisions: int = 0
    model_calls: int = 0
    tools: int = 0
    input_reserved: int = 0
    output_reserved: int = 0
    actual_input: int = 0
    actual_output: int = 0
    unknown_usage_calls: int = 0
    started: float = field(default_factory=time.monotonic)

    def check(self) -> None:
        if self.cancelled():
            raise RunStopped("cancelled")
        if time.monotonic() - self.started >= self.seconds:
            raise RunStopped("deadline")

    def decision(self) -> None:
        self.check()
        if self.decisions >= self.max_decisions:
            raise RunStopped("decision_budget")
        self.decisions += 1

    def tool(self) -> None:
        self.check()
        if self.tools >= self.max_tools:
            raise RunStopped("tool_budget")
        self.tools += 1

    def reserve(self, messages: list[BaseMessage], schemas: list[dict],
                *, output_cap: int = 512) -> tuple[dict, int]:
        self.check()
        if self.model_calls >= self.max_calls:
            raise RunStopped("model_budget")
        output = min(output_cap, self.max_output - self.output_reserved)
        size = input_bound(messages, schemas)
        if output < 128 or self.input_reserved + size > self.max_input:
            raise RunStopped("token_budget")
        if size > self.prompt_limit(output_cap):
            raise RunStopped("context_budget")
        self.input_reserved += size
        self.output_reserved += output
        self.model_calls += 1
        return ({"timeout": min(30.0, self.seconds - (time.monotonic() - self.started)),
                 "max_output_tokens": output}, size)

    def prompt_limit(self, output_cap: int) -> int:
        """Use the same remaining output allowance when fitting and reserving."""
        return min(self.max_prompt, self.context_window - min(output_cap, self.max_output - self.output_reserved))

    def record(self, reply: AIMessage, reserved_input: int, reserved_output: int) -> None:
        usage = reply.usage_metadata
        if usage is None:
            self.unknown_usage_calls += 1
        else:
            self.actual_input += usage["input_tokens"]
            self.actual_output += usage["output_tokens"]
            self.input_reserved += usage["input_tokens"] - reserved_input
            self.output_reserved += usage["output_tokens"] - reserved_output
            if self.actual_input > self.max_input or self.actual_output > self.max_output:
                raise RunStopped("token_budget")
        self.check()

    def summary(self) -> dict:
        result = {key: getattr(self, key) for key in
                ("decisions", "model_calls", "tools", "input_reserved", "output_reserved",
                 "actual_input", "actual_output", "unknown_usage_calls")}
        if self.unknown_usage_calls:
            result["actual_input"] = result["actual_output"] = None
        return result
