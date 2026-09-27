"""Offline provider boundary tests; these never contact a model service."""

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

TOOL = {
    "type": "function",
    "function": {
        "name": "read_source",
        "description": "Read selected evidence.",
        "parameters": {
            "type": "object",
            "properties": {
                "evidence_ids": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["evidence_ids"],
            "additionalProperties": False,
        },
    },
}
SCHEMA = {
    "title": "MemoryFacts",
    "description": "Extract observed memory.",
    "type": "object",
    "properties": {"memory_gb": {"type": "integer"}},
    "required": ["memory_gb"],
    "additionalProperties": False,
}
MESSAGES = [HumanMessage(content="核对内存")]


class RecordingChatModel(BaseChatModel):
    responses: list[Any] = Field(default_factory=list, exclude=True, repr=False)
    requests: list[dict] = Field(default_factory=list, exclude=True, repr=False)
    max_retries: int = 5

    @property
    def _llm_type(self) -> str:
        return "recording-offline"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)

    def _generate(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
    ):
        self.requests.append(
            {"messages": messages, "retries": self.max_retries, **kwargs}
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ChatResult(generations=[ChatGeneration(message=response)])


def tool_response(name="read_source", args=None):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args or {"evidence_ids": ["e1"]}, "id": "c1"}
        ],
        usage_metadata={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
    )


def test_decide_passes_messages_tools_deadline_and_output_limit():
    from backend.app.providers import ChatModelProvider, Provider

    model = RecordingChatModel(responses=[tool_response()])
    provider = ChatModelProvider(model)
    assert isinstance(provider, Provider)
    result = provider.decide(MESSAGES, [TOOL], timeout=2.5, max_output_tokens=128)
    assert result.tool_calls[0]["args"] == {"evidence_ids": ["e1"]}
    assert result.usage_metadata["input_tokens"] == 20
    request = model.requests[0]
    assert request["messages"] == MESSAGES
    assert request["tools"] == [TOOL]
    assert request["tool_choice"] == "required"
    assert request["timeout"] == 2.5
    assert request["max_tokens"] == 128
    assert request["retries"] == 0
    assert model.max_retries == 5  # the caller's model is not mutated


def test_extract_forces_one_schema_tool_and_returns_ai_message():
    from backend.app.providers import ChatModelProvider

    model = RecordingChatModel(
        responses=[tool_response("MemoryFacts", {"memory_gb": 16})]
    )
    result = ChatModelProvider(model).extract(
        MESSAGES, SCHEMA, timeout=3, max_output_tokens=50
    )
    assert result.tool_calls[0]["args"] == {"memory_gb": 16}
    assert model.requests[0]["tool_choice"] == "MemoryFacts"
    assert model.requests[0]["parallel_tool_calls"] is False


@pytest.mark.parametrize(
    "response",
    [
        tool_response(args={"evidence_ids": "e1"}),
        tool_response(args={"evidence_ids": ["e1"], "path": "/secret"}),
        tool_response(name="execute_shell"),
        AIMessage(
            content="",
            invalid_tool_calls=[
                {"name": "read_source", "args": "{bad", "id": "c1", "error": "bad JSON"}
            ],
        ),
    ],
)
def test_invalid_tool_output_is_rejected_without_retries(response):
    from backend.app.providers import ChatModelProvider, ProviderError

    model = RecordingChatModel(responses=[response])
    with pytest.raises(ProviderError) as exc:
        ChatModelProvider(model).decide(
            MESSAGES, [TOOL], timeout=1, max_output_tokens=50
        )
    assert exc.value.code == "invalid_output"
    assert len(model.requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        AIMessage(content='{"memory_gb":16}'),
        tool_response("MemoryFacts", {"memory_gb": "16"}),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "MemoryFacts", "args": {"memory_gb": 16}, "id": "c1"},
                {"name": "MemoryFacts", "args": {"memory_gb": 32}, "id": "c2"},
            ],
        ),
    ],
)
def test_extract_rejects_unstructured_invalid_or_multiple_results(response):
    from backend.app.providers import ChatModelProvider, ProviderError

    model = RecordingChatModel(responses=[response])
    with pytest.raises(ProviderError, match="invalid_output"):
        ChatModelProvider(model).extract(
            MESSAGES, SCHEMA, timeout=1, max_output_tokens=50
        )


@pytest.mark.parametrize(("response", "detail"), [
    (AIMessage(content="", invalid_tool_calls=[
        {"name": "MemoryFacts", "args": "{sk-secret", "id": "c1", "error": "bad JSON"}
    ], response_metadata={"finish_reason": "length"}), "output_limit"),
    (AIMessage(content="sk-secret"), "missing_tool_call"),
    (tool_response("MemoryFacts", {"memory_gb": "sk-secret"}), "tool_schema"),
])
def test_extract_invalid_output_exposes_only_safe_category(response, detail):
    from backend.app.providers import ChatModelProvider, ProviderError

    model = RecordingChatModel(responses=[response])
    with pytest.raises(ProviderError) as caught:
        ChatModelProvider(model).extract(
            MESSAGES, SCHEMA, timeout=1, max_output_tokens=50
        )
    assert caught.value.code == "invalid_output"
    assert caught.value.detail == detail
    assert "sk-secret" not in repr(caught.value)


def test_unexpected_finish_reason_type_cannot_escape_provider_error_boundary():
    from backend.app.providers import ChatModelProvider, ProviderError

    response = AIMessage(content="", invalid_tool_calls=[
        {"name": "MemoryFacts", "args": "{sk-secret", "id": "c1", "error": "bad JSON"}
    ], response_metadata={"finish_reason": []})
    model = RecordingChatModel(responses=[response])
    with pytest.raises(ProviderError) as caught:
        ChatModelProvider(model).extract(MESSAGES, SCHEMA, timeout=1, max_output_tokens=50)
    assert caught.value.code == "invalid_output"
    assert caught.value.detail == "malformed_tool_args"


def test_timeout_is_sanitized_unknown_and_never_replayed():
    from backend.app.providers import ChatModelProvider, ProviderError

    model = RecordingChatModel(responses=[TimeoutError("sk-secret raw response")])
    with pytest.raises(ProviderError) as exc:
        ChatModelProvider(model).decide(
            MESSAGES, [TOOL], timeout=0.1, max_output_tokens=50
        )
    assert exc.value.code == "timeout"
    assert exc.value.outcome_unknown is True
    assert "secret" not in str(exc.value)
    assert "secret" not in repr(exc.value)
    assert len(model.requests) == 1


@pytest.mark.parametrize(
    "timeout,tokens", [(0, 20), (float("inf"), 20), (1, 0), (1, True)]
)
def test_invalid_limits_fail_before_transport(timeout, tokens):
    from backend.app.providers import ChatModelProvider, ProviderError

    model = RecordingChatModel()
    with pytest.raises(ProviderError, match="invalid_request"):
        ChatModelProvider(model).decide(
            MESSAGES, [TOOL], timeout=timeout, max_output_tokens=tokens
        )
    assert model.requests == []


def test_invalid_schema_fails_before_transport():
    from backend.app.providers import ChatModelProvider, ProviderError

    model = RecordingChatModel()
    with pytest.raises(ProviderError, match="invalid_request"):
        ChatModelProvider(model).extract(
            MESSAGES, {"type": "nonsense"}, timeout=1, max_output_tokens=20
        )
    assert model.requests == []


def test_fake_scripts_are_separate_and_exhaustion_has_no_fallback():
    from backend.app.providers import FakeProvider, Provider, ProviderError

    fake = FakeProvider(
        responses=[tool_response(), ProviderError("timeout", outcome_unknown=True)],
        extract_responses=[tool_response("MemoryFacts", {"memory_gb": 16})],
    )
    assert isinstance(fake, Provider)
    assert fake.decide(MESSAGES, [TOOL], timeout=2, max_output_tokens=30).tool_calls
    assert fake.extract(MESSAGES, SCHEMA, timeout=1, max_output_tokens=20).tool_calls
    with pytest.raises(ProviderError, match="timeout"):
        fake.decide(MESSAGES, [TOOL], timeout=1, max_output_tokens=20)
    with pytest.raises(ProviderError, match="fake_exhausted"):
        fake.decide(MESSAGES, [TOOL], timeout=1, max_output_tokens=20)
    assert [c["operation"] for c in fake.calls] == [
        "decide",
        "extract",
        "decide",
        "decide",
    ]
    assert fake.calls[0]["timeout"] == 2
    assert fake.calls[0]["max_output_tokens"] == 30


def valid_environment():
    return {
        "DOCUCHECK_MODEL_PROVIDER": "openai-compatible",
        "DOCUCHECK_MODEL_ID": "explicit-test-model",
        "DOCUCHECK_MODEL_BASE_URL": "https://models.example.test/v1",
        "DOCUCHECK_MODEL_API_KEY": "sk-private-do-not-log",
        "DOCUCHECK_MODEL_CONTEXT_WINDOW": "32000",
    }


@pytest.mark.parametrize("key", list(valid_environment()))
def test_factory_rejects_missing_config_without_default_or_fake(key):
    from backend.app.providers import ProviderError, configured_provider

    environ = valid_environment()
    del environ[key]
    with pytest.raises(ProviderError, match="configuration_error"):
        configured_provider(environ=environ)


def test_config_and_provider_repr_hide_key():
    from backend.app.providers import ProviderConfig, ProviderError

    config = ProviderConfig.from_env(valid_environment())
    assert config.context_window == 32000
    assert "sk-private" not in repr(config)
    assert "sk-private" not in str(config)
    assert "sk-private" not in str(ProviderError("sk-private"))


@pytest.mark.parametrize(
    "key,value",
    [
        ("DOCUCHECK_MODEL_PROVIDER", "fake"),
        ("DOCUCHECK_MODEL_CONTEXT_WINDOW", "0"),
        ("DOCUCHECK_MODEL_CONTEXT_WINDOW", "unbounded"),
        ("DOCUCHECK_MODEL_BASE_URL", "file:///secret"),
        ("DOCUCHECK_MODEL_BASE_URL", "http://remote.example.test/v1"),
        ("DOCUCHECK_MODEL_BASE_URL", "https://key:secret@example.test/v1"),
    ],
)
def test_invalid_config_is_sanitized(key, value):
    from backend.app.providers import ProviderConfig, ProviderError

    environ = valid_environment()
    environ[key] = value
    with pytest.raises(ProviderError, match="configuration_error") as exc:
        ProviderConfig.from_env(environ)
    assert "secret" not in str(exc.value)


def test_local_http_endpoint_is_allowed_for_local_model_server():
    from backend.app.providers import ProviderConfig

    environ = valid_environment()
    environ["DOCUCHECK_MODEL_BASE_URL"] = "http://127.0.0.1:8000/v1"
    assert ProviderConfig.from_env(environ).base_url == "http://127.0.0.1:8000/v1"


def test_injected_openai_timeout_reaches_http_once_without_sdk_retries():
    import httpx
    from langchain_openai import ChatOpenAI

    from backend.app.providers import ChatModelProvider, ProviderError

    requests = []

    def fail(request):
        requests.append(request)
        raise httpx.ReadTimeout("sk-secret raw body", request=request)

    model = ChatOpenAI(
        model="explicit-test-model",
        api_key="sk-secret",
        base_url="https://offline.test/v1",
        max_retries=2,
        http_client=httpx.Client(transport=httpx.MockTransport(fail)),
    )
    with pytest.raises(ProviderError) as exc:
        ChatModelProvider(model).decide(
            MESSAGES, [TOOL], timeout=0.01, max_output_tokens=32
        )
    assert exc.value.code == "timeout"
    assert exc.value.outcome_unknown is True
    assert len(requests) == 1
    assert requests[0].extensions["timeout"]["read"] == 0.01


def test_openai_adapter_passes_tool_schema_token_limit_and_usage_via_http():
    import json

    import httpx
    from langchain_openai import ChatOpenAI

    from backend.app.providers import ChatModelProvider

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "explicit-test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_source",
                                        "arguments": '{"evidence_ids":["e1"]}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 12,
                    "total_tokens": 52,
                },
            },
        )

    model = ChatOpenAI(
        model="explicit-test-model",
        api_key="sk-secret",
        base_url="https://offline.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    provider = ChatModelProvider(model)
    response = provider.extract(MESSAGES, TOOL, timeout=2, max_output_tokens=32)
    assert response.usage_metadata["total_tokens"] == 52
    assert requests[0]["tools"] == [TOOL]
    assert requests[0]["tool_choice"]["function"]["name"] == "read_source"
    assert requests[0]["max_completion_tokens"] == 32
    assert "sk-secret" not in repr(provider)


def test_configured_adapter_constructs_without_network_and_without_defaults():
    from backend.app.providers import configured_provider

    provider = configured_provider(environ=valid_environment())
    assert provider.context_window == 32000
    assert "sk-private" not in repr(provider)


def test_configured_deepseek_adapter_disables_thinking_for_forced_tools():
    from backend.app.providers import configured_provider

    environ = valid_environment()
    environ["DOCUCHECK_MODEL_ID"] = "deepseek-flash"
    environ["DOCUCHECK_MODEL_BASE_URL"] = "https://api.deepseek.com"
    provider = configured_provider(environ=environ)
    assert provider._model.extra_body == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize("operation", ["extract", "decide"])
def test_deepseek_adapter_sends_supported_token_limit_and_tool_choice(operation):
    import json

    import httpx

    from backend.app.providers import ChatModelProvider
    from backend.app.providers.deepseek import DeepSeekChatOpenAI

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-flash",
                "choices": [{
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "MemoryFacts",
                                "arguments": '{"memory_gb":16}',
                            },
                        }],
                    },
                }],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 12,
                    "total_tokens": 52,
                },
            },
        )

    model = DeepSeekChatOpenAI(
        model="deepseek-flash",
        api_key="sk-secret",
        base_url="https://api.deepseek.com",
        max_retries=0,
        extra_body={"thinking": {"type": "disabled"}},
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    provider = ChatModelProvider(model)
    response = (
        provider.extract(MESSAGES, SCHEMA, timeout=2, max_output_tokens=32)
        if operation == "extract"
        else provider.decide(MESSAGES, [SCHEMA], timeout=2, max_output_tokens=32)
    )
    assert response.tool_calls[0]["args"] == {"memory_gb": 16}
    assert len(requests) == 1
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["max_tokens"] == 32
    assert "max_completion_tokens" not in requests[0]
    assert "parallel_tool_calls" not in requests[0]
    if operation == "extract":
        assert requests[0]["tool_choice"]["function"]["name"] == "MemoryFacts"
    else:
        assert requests[0]["tool_choice"] == "required"


@pytest.mark.parametrize(
    "status,code,unknown",
    [
        (401, "authentication_error", False),
        (429, "rate_limit", False),
        (503, "provider_error", True),
    ],
)
def test_openai_http_errors_are_sanitized_and_not_retried(status, code, unknown):
    import traceback

    import httpx
    from langchain_openai import ChatOpenAI

    from backend.app.providers import ChatModelProvider, ProviderError

    requests = []

    def fail(request):
        requests.append(request)
        return httpx.Response(
            status, json={"error": {"message": "sk-secret raw response"}}
        )

    model = ChatOpenAI(
        model="explicit-test-model",
        api_key="sk-secret",
        base_url="https://offline.test/v1",
        max_retries=2,
        http_client=httpx.Client(transport=httpx.MockTransport(fail)),
    )
    with pytest.raises(ProviderError) as exc:
        ChatModelProvider(model).decide(
            MESSAGES, [TOOL], timeout=1, max_output_tokens=32
        )
    assert exc.value.code == code
    assert exc.value.outcome_unknown is unknown
    assert len(requests) == 1
    assert "raw response" not in "".join(traceback.format_exception(exc.value))
