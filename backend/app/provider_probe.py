"""Single-request capability probe using only synthetic procurement text."""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage

from .providers import ProviderError, configured_provider

SCHEMA = {
    "title": "MemoryProbe",
    "description": "Extract the memory size stated in the synthetic sentence.",
    "type": "object",
    "properties": {"memory_gb": {"type": "integer"}},
    "required": ["memory_gb"],
    "additionalProperties": False,
}


def main() -> int:
    """Return 0 only when a real provider produces one valid forced tool call."""
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    try:
        provider = configured_provider()
        response = provider.extract(
            [
                SystemMessage(content="只根据用户的模拟句子抽取内存容量，调用指定工具。"),
                HumanMessage(content="模拟句子：已装内存 16 GB"),
            ],
            SCHEMA,
            timeout=30,
            max_output_tokens=128,
        )
        call = response.tool_calls[0]
        if call["name"] != "MemoryProbe" or call["args"]["memory_gb"] != 16:
            raise ProviderError("invalid_output")
    except ProviderError as exc:
        print(f"probe_failed: {exc.code}", file=sys.stderr)
        return 2
    usage = response.usage_metadata or {}
    print(
        json.dumps(
            {
                "tool_call": call["name"],
                "memory_gb": call["args"]["memory_gb"],
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
