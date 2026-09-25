"""Explicit offline scripted provider, never selected by configuration."""

from collections import deque
from collections.abc import Iterable
from copy import deepcopy

from langchain_core.messages import AIMessage, BaseMessage

from .base import ProviderError
from .chat_model import normalize_tools, validate_request, validate_response


class FakeProvider:
    def __init__(
        self,
        responses: Iterable[AIMessage | ProviderError] = (),
        *,
        extract_responses: Iterable[AIMessage | ProviderError] = (),
        context_window: int = 32768,
    ) -> None:
        self._responses = deque(responses)
        self._extract_responses = deque(extract_responses)
        self.calls: list[dict] = []
        self.context_window = context_window

    def decide(
        self,
        messages: list[BaseMessage],
        tools: list[dict],
        *,
        timeout: float,
        max_output_tokens: int,
    ) -> AIMessage:
        validate_request(messages, timeout, max_output_tokens)
        normalized = normalize_tools(tools)
        self.calls.append(
            deepcopy(
                {
                    "operation": "decide",
                    "messages": messages,
                    "tools": tools,
                    "timeout": timeout,
                    "max_output_tokens": max_output_tokens,
                }
            )
        )
        return self._next(self._responses, normalized, extraction=False)

    def extract(
        self,
        messages: list[BaseMessage],
        schema: dict,
        *,
        timeout: float,
        max_output_tokens: int,
    ) -> AIMessage:
        validate_request(messages, timeout, max_output_tokens)
        normalized = normalize_tools([schema])
        self.calls.append(
            deepcopy(
                {
                    "operation": "extract",
                    "messages": messages,
                    "schema": schema,
                    "timeout": timeout,
                    "max_output_tokens": max_output_tokens,
                }
            )
        )
        return self._next(self._extract_responses, normalized, extraction=True)

    @staticmethod
    def _next(
        script: deque[AIMessage | ProviderError],
        tools: list[dict],
        *,
        extraction: bool,
    ) -> AIMessage:
        if not script:
            raise ProviderError("fake_exhausted")
        response = script.popleft()
        if isinstance(response, ProviderError):
            raise response
        return validate_response(response, tools, extraction=extraction)
