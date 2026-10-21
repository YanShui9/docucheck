"""One budget shared by decisions, extraction and tool execution."""
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage

MODE_LIMITS = {
    "standard": {"max_decisions": 8, "max_tools": 16, "max_calls": 16},
    "thorough": {"max_decisions": 12, "max_tools": 24, "max_calls": 32},
}


class RunStopped(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def quota_stop_message(code: str) -> str | None:
    if code == "input_budget":
        return "本次处理已达到配置的累计输入用量上限。已完成的处理保留，请调整任务用量限额后重新处理。"
    if code == "output_budget":
        return "本次处理已达到配置的累计输出用量上限。已完成的处理保留，请调整任务用量限额后重新处理。"
    return None


def input_bound(messages: list[BaseMessage], schemas: list[dict]) -> int:
    # Legacy byte estimate, not an exact tokenizer or the SDK wire payload.
    # Admission conservatively allows one estimated byte per configured token.
    # Actual provider usage is separate; no precise token percentage is implied.
    body = [{"role": m.type, "content": m.content,
             "tool_calls": getattr(m, "tool_calls", [])} for m in messages]
    return len(json.dumps([body, schemas], ensure_ascii=False).encode("utf-8")) + 512


@dataclass
class Budget:
    max_decisions: int = 8
    context_window: int = 32768
    cancelled: Callable[[], bool] = lambda: False
    max_input: int | None = None
    max_output: int | None = None
    max_prompt: int | None = None
    safety_percent: int = 5
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

    def __post_init__(self) -> None:
        if type(self.context_window) is not int or self.context_window <= 0:
            raise ValueError("invalid_context_capacity")
        for value in (self.max_input, self.max_output, self.max_prompt):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError("invalid_context_capacity")
        if type(self.safety_percent) is not int or not 0 <= self.safety_percent < 100:
            raise ValueError("invalid_context_capacity")
        if self.max_prompt is None:
            self.max_prompt = self.context_window

    @classmethod
    def for_provider(cls, provider, mode: str, **overrides) -> "Budget":
        return cls(**(MODE_LIMITS[mode] | {
            "context_window": getattr(provider, "context_window", 32768),
            "max_input": getattr(provider, "run_max_input_tokens", None),
            "max_output": getattr(provider, "run_max_output_tokens", None),
            "safety_percent": getattr(provider, "context_safety_percent", 5),
        } | overrides))

    @property
    def safety_reserved(self) -> int:
        return (self.context_window * self.safety_percent + 99) // 100

    def planned_output(self, output_cap: int) -> int:
        if type(output_cap) is not int or output_cap <= 0:
            raise ValueError("invalid_output_cap")
        return (output_cap if self.max_output is None else
                max(0, min(output_cap, self.max_output - self.output_reserved)))

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
        output = self.planned_output(output_cap)
        size = input_bound(messages, schemas)
        if output < 128:
            raise RunStopped("output_budget")
        if self.max_input is not None and self.input_reserved + size > self.max_input:
            raise RunStopped("input_budget")
        if size > self.prompt_limit(output_cap):
            raise RunStopped("context_budget")
        self.input_reserved += size
        self.output_reserved += output
        self.model_calls += 1
        return ({"timeout": min(30.0, self.seconds - (time.monotonic() - self.started)),
                 "max_output_tokens": output}, size)

    def prompt_limit(self, output_cap: int) -> int:
        """Use the same remaining output allowance when fitting and reserving."""
        return min(self.max_prompt, self.context_window - self.planned_output(output_cap) - self.safety_reserved)

    def record(self, reply: AIMessage, reserved_input: int, reserved_output: int) -> None:
        usage = reply.usage_metadata
        if usage is None:
            self.unknown_usage_calls += 1
        else:
            self.actual_input += usage["input_tokens"]
            self.actual_output += usage["output_tokens"]
            self.input_reserved += usage["input_tokens"] - reserved_input
            self.output_reserved += usage["output_tokens"] - reserved_output
            if self.max_input is not None and self.actual_input > self.max_input:
                raise RunStopped("input_budget")
            if self.max_output is not None and self.actual_output > self.max_output:
                raise RunStopped("output_budget")
        self.check()

    def summary(self) -> dict:
        result = {key: getattr(self, key) for key in
                ("decisions", "model_calls", "tools", "input_reserved", "output_reserved",
                 "actual_input", "actual_output", "unknown_usage_calls")}
        if self.unknown_usage_calls:
            result["actual_input"] = result["actual_output"] = None
        return result
