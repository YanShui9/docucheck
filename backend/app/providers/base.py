"""Provider contract. Errors contain safe codes, never transport response bodies."""

from typing import Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage


class ProviderError(Exception):
    """An externally safe error; outcome_unknown forbids automatic replay."""

    _CODES = frozenset(
        {
            "configuration_error",
            "provider_unavailable",
            "authentication_error",
            "rate_limit",
            "timeout",
            "invalid_request",
            "invalid_output",
            "connection_error",
            "provider_error",
            "fake_exhausted",
        }
    )
    _DETAILS = frozenset({
        "output_limit", "malformed_tool_args", "missing_tool_call",
        "multiple_tool_calls", "tool_schema", "message_type",
        "tls_certificate_verification",
    })

    def __init__(self, code: str, outcome_unknown: bool = False,
                 detail: str | None = None) -> None:
        self.code = code if code in self._CODES else "provider_error"
        self.outcome_unknown = outcome_unknown
        self.detail = detail if isinstance(detail, str) and detail in self._DETAILS else None
        super().__init__(self.code)


@runtime_checkable
class Provider(Protocol):
    def decide(
        self,
        messages: list[BaseMessage],
        tools: list[dict],
        *,
        timeout: float,
        max_output_tokens: int,
    ) -> AIMessage: ...

    def extract(
        self,
        messages: list[BaseMessage],
        schema: dict,
        *,
        timeout: float,
        max_output_tokens: int,
    ) -> AIMessage: ...
