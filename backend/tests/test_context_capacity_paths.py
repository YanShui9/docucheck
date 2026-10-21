"""All agent entry points inherit configured capacity and explicit quotas."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.dynamic import DynamicAgent
from backend.app.agent.graph import AgentSession
from backend.app.agent.policy import input_bound
from backend.app.evidence.repository import load_case
from backend.app.providers import ProviderConfig, ProviderError, configured_provider
from backend.tests.test_agent_graph import PACK
from backend.tests.test_dynamic_agent import definition, repository
from backend.tests.test_provider_contract import valid_environment


class CaptureUnknown:
    context_window = 1_000_000
    run_max_input_tokens = None
    run_max_output_tokens = None
    context_safety_percent = 5

    def __init__(self, quota=None):
        self.run_max_input_tokens = quota
        self.calls = []

    def decide(self, messages, schemas, **options):
        self.calls.append((messages, schemas, options))
        raise ProviderError("timeout", outcome_unknown=True)


def invoke(kind, provider, question, *, with_history=False):
    history = [(HumanMessage(content="之前的采购讨论"), AIMessage(content="记录" * 10000))] if with_history else []
    if kind == "assistant":
        return AssistantAgent(repository(), provider, history=history).ask(question)
    if kind == "dynamic":
        return DynamicAgent(repository(), provider, definition(), history=history).ask(question)
    session = AgentSession(load_case(PACK), provider)
    session.history = history
    result = session.ask(question)
    return {"status": result.status, "error": result.error, "answer": result.answer,
            "usage": result.usage, "events": result.events}


@pytest.mark.parametrize("kind", ["assistant", "dynamic", "legacy"])
def test_model_capacity_is_used_without_replaying_unknown_requests(kind):
    provider = CaptureUnknown()
    result = invoke(kind, provider, "请读取采购资料", with_history=True)
    assert len(provider.calls) == 1, result
    messages, schemas, options = provider.calls[0]
    assert input_bound(messages, schemas) > 16000
    assert options["max_output_tokens"] >= 512
    assert result["usage"]["model_calls"] == result["usage"]["unknown_usage_calls"] == 1
    assert result["usage"]["actual_input"] is None


@pytest.mark.parametrize("kind", ["assistant", "dynamic", "legacy"])
@pytest.mark.parametrize("quota,code,label", [("input", "input_budget", "累计输入"),
                                               ("output", "output_budget", "累计输出")])
def test_explicit_quota_rejects_locally_on_every_entry_point(kind, quota, code, label):
    provider = CaptureUnknown(quota=600 if quota == "input" else None)
    if quota == "output":
        provider.run_max_output_tokens = 100
    result = invoke(kind, provider, "读取资料")
    assert provider.calls == []
    assert result["error"] == code
    assert label in result["answer"] and code not in result["answer"]
    assert result["usage"]["model_calls"] == result["usage"]["unknown_usage_calls"] == 0
    events = [event for event in result["events"] if event.get("kind") == "model"]
    assert events[-1]["provider_called"] is False


def test_optional_provider_quotas_are_absent_by_default():
    config = ProviderConfig.from_env(valid_environment())
    assert config.run_max_input_tokens is None and config.run_max_output_tokens is None
    assert config.context_safety_percent == 5


def test_blank_optional_provider_quotas_are_unset_for_compose_defaults():
    environ = valid_environment()
    environ.update(DOCUCHECK_RUN_MAX_INPUT_TOKENS="", DOCUCHECK_RUN_MAX_OUTPUT_TOKENS="  ")
    config = ProviderConfig.from_env(environ)
    assert config.run_max_input_tokens is None and config.run_max_output_tokens is None


def test_explicit_provider_config_reaches_runtime_provider_without_network():
    environ = valid_environment()
    environ.update(DOCUCHECK_RUN_MAX_INPUT_TOKENS="80000", DOCUCHECK_RUN_MAX_OUTPUT_TOKENS="9000",
                   DOCUCHECK_MODEL_CONTEXT_SAFETY_PERCENT="7")
    provider = configured_provider(environ=environ)
    assert provider.run_max_input_tokens == 80000 and provider.run_max_output_tokens == 9000
    assert provider.context_safety_percent == 7


@pytest.mark.parametrize("key", ["DOCUCHECK_RUN_MAX_INPUT_TOKENS", "DOCUCHECK_RUN_MAX_OUTPUT_TOKENS"])
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "secret", True, "2147483648"])
def test_invalid_explicit_quota_is_sanitized(key, value):
    environ = valid_environment()
    environ[key] = value
    with pytest.raises(ProviderError, match="configuration_error") as exc:
        ProviderConfig.from_env(environ)
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("value", ["", "-1", "100", "1.5", "secret", True])
def test_invalid_safety_margin_is_sanitized(value):
    environ = valid_environment()
    environ["DOCUCHECK_MODEL_CONTEXT_SAFETY_PERCENT"] = value
    with pytest.raises(ProviderError, match="configuration_error"):
        ProviderConfig.from_env(environ)


@pytest.mark.parametrize("percent", [0, 5, 99])
def test_valid_safety_margin_reaches_budget_without_using_token_percentages(percent):
    from backend.app.agent.policy import Budget

    provider = CaptureUnknown()
    provider.context_safety_percent = percent
    budget = Budget.for_provider(provider, "standard")
    assert budget.safety_reserved == 10000 * percent
    assert budget.prompt_limit(512) == 1_000_000 - 10000 * percent - 512
