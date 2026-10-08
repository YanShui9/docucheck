"""A single LangChain call per request, with local validation and no retries."""

import math
import ssl

from jsonschema import Draft202012Validator
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langsmith import tracing_context

from .base import ProviderError


def validate_request(
    messages: list[BaseMessage], timeout: float, max_output_tokens: int
) -> None:
    if (
        not isinstance(messages, list)
        or not messages
        or not all(isinstance(message, BaseMessage) for message in messages)
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
        or type(max_output_tokens) is not int
        or max_output_tokens <= 0
    ):
        raise ProviderError("invalid_request")


def normalize_tools(tools: list[dict]) -> list[dict]:
    try:
        if not isinstance(tools, list) or not all(
            isinstance(tool, dict) for tool in tools
        ):
            raise ValueError
        normalized = [convert_to_openai_tool(tool) for tool in tools]
        names = []
        for tool in normalized:
            function = tool["function"]
            if not isinstance(function["name"], str) or not function["name"]:
                raise ValueError
            parameters = function["parameters"]
            if parameters.get("type") != "object":
                raise ValueError
            Draft202012Validator.check_schema(parameters)
            _reject_remote_refs(parameters)
            names.append(function["name"])
        if len(names) != len(set(names)):
            raise ValueError
        return normalized
    except Exception:  # noqa: BLE001 - schema library errors must not expose input
        raise ProviderError("invalid_request") from None


def _reject_remote_refs(value: object) -> None:
    """Validation may resolve local definitions, but must never fetch a URL."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef"} and (
                not isinstance(item, str) or not item.startswith("#")
            ):
                raise ValueError
            _reject_remote_refs(item)
    elif isinstance(value, list):
        for item in value:
            _reject_remote_refs(item)


def validate_response(
    response: AIMessage, tools: list[dict], *, extraction: bool
) -> AIMessage:
    if not isinstance(response, AIMessage):
        raise ProviderError("invalid_output", detail="message_type")
    if response.response_metadata.get("finish_reason") in ("length", "max_tokens"):
        raise ProviderError("invalid_output", detail="output_limit")
    if response.invalid_tool_calls:
        raise ProviderError("invalid_output", detail="malformed_tool_args")
    if extraction and not response.tool_calls:
        raise ProviderError("invalid_output", detail="missing_tool_call")
    if extraction and len(response.tool_calls) != 1:
        raise ProviderError("invalid_output", detail="multiple_tool_calls")
    schemas = {
        tool["function"]["name"]: tool["function"]["parameters"] for tool in tools
    }
    ids: set[str] = set()
    try:
        for call in response.tool_calls:
            if not call.get("id") or call["id"] in ids:
                raise ValueError
            ids.add(call["id"])
            Draft202012Validator(schemas[call["name"]]).validate(call["args"])
    except Exception:  # noqa: BLE001 - sanitize all malformed provider payloads
        raise ProviderError("invalid_output", detail="tool_schema") from None
    return response


def _tls_verification_failed(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _transport_error(error: Exception) -> ProviderError:
    # Inspect types/status only. Never copy messages, response bodies or headers.
    names = {kind.__name__ for kind in type(error).__mro__}
    status = getattr(error, "status_code", None)
    if isinstance(error, TimeoutError) or names & {
        "APITimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
        "OpenAITimeoutError",
    }:
        return ProviderError("timeout", outcome_unknown=True)
    if status in {401, 403} or names & {"AuthenticationError", "PermissionDeniedError"}:
        return ProviderError("authentication_error")
    if status == 429 or "RateLimitError" in names:
        return ProviderError("rate_limit")
    if status in {400, 404, 422}:
        return ProviderError("invalid_request")
    if isinstance(error, ConnectionError) or names & {
        "APIConnectionError",
        "ConnectError",
        "OpenAIConnectionError",
    }:
        return ProviderError("connection_error", outcome_unknown=True,
                             detail="tls_certificate_verification" if _tls_verification_failed(error) else None)
    return ProviderError("provider_error", outcome_unknown=True)


class ChatModelProvider:
    """Wrap an injected model; callers reserve shared budgets before invoking."""

    def __init__(self, model: BaseChatModel, *, context_window: int = 32768) -> None:
        updates = {"callbacks": None, "tags": None, "metadata": None, "verbose": False}
        if "max_retries" in type(model).model_fields:
            updates["max_retries"] = 0
        # OpenAI constructs its SDK clients at model initialization. Changing
        # only the LangChain field leaves those clients' retry policies intact.
        root_client = getattr(model, "root_client", None)
        if root_client is not None and hasattr(root_client, "with_options"):
            client = root_client.with_options(max_retries=0)
            updates.update(root_client=client, client=client.chat.completions)
        self._model = model.model_copy(update=updates)
        self.context_window = context_window
        self.model_id = getattr(model, "model_name", None)

    def __repr__(self) -> str:
        return f"ChatModelProvider(context_window={self.context_window})"

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
        return self._invoke(
            messages, normalized, timeout, max_output_tokens, extraction=False
        )

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
        return self._invoke(
            messages, normalized, timeout, max_output_tokens, extraction=True
        )

    def _invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict],
        timeout: float,
        max_output_tokens: int,
        *,
        extraction: bool,
    ) -> AIMessage:
        try:
            options = {"parallel_tool_calls": False}
            if tools:
                options["tool_choice"] = (
                    tools[0]["function"]["name"] if extraction else "required"
                )
            # This boundary does not opt into external tracing, even when the
            # surrounding shell enables LangSmith globally.
            with tracing_context(enabled=False):
                bound = (
                    self._model.bind_tools(tools, **options) if tools else self._model
                )
                response = bound.invoke(
                    messages,
                    config={"callbacks": []},
                    timeout=timeout,
                    max_tokens=max_output_tokens,
                )
        except Exception as error:  # noqa: BLE001 - external transport boundary
            raise _transport_error(error) from None
        return validate_response(response, tools, extraction=extraction)
