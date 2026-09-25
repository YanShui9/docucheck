"""DeepSeek Chat Completions request shape for the pinned LangChain adapter."""

from typing import Any

from langchain_openai import ChatOpenAI


class DeepSeekChatOpenAI(ChatOpenAI):
    """Use DeepSeek's documented token field and omit unsupported tool option."""

    def _get_request_payload(
        self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        payload.pop("parallel_tool_calls", None)
        return payload
