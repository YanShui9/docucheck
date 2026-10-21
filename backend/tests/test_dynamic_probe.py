"""The default probe is an inspectable offline preview with a hard request cap."""

import json

import pytest

from backend.app import dynamic_probe
from backend.app.providers.base import ProviderError


def test_preview_never_configures_or_calls_provider(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        dynamic_probe,
        "configured_provider",
        lambda: pytest.fail("preview sent a request"),
    )
    path = tmp_path / "preview.json"
    assert dynamic_probe.main(["--output", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["max_model_requests"] == 6
    assert report["data"] == "synthetic_only" and not report["confirms_user_checklist"]
    assert len(report["source_text"]) == 2
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_probe_cap_counts_attempts_and_blocks_seventh_call():
    class Provider:
        context_window = 32768

        def decide(self):
            return None

    capped = dynamic_probe.CappedProvider(Provider())
    for _ in range(6):
        capped.decide()
    with pytest.raises(ProviderError):
        capped.decide()
    assert capped.calls == 6


@pytest.mark.parametrize(
    "settings,code",
    [
        ({"run_max_input_tokens": 600}, "input_budget"),
        ({"run_max_output_tokens": 100}, "output_budget"),
        ({"context_safety_percent": 99}, "context_budget"),
    ],
)
def test_probe_keeps_configured_limits_before_sending(settings, code):
    class Provider:
        context_window = 32768

        def decide(self, *args, **kwargs):
            pytest.fail("configured limit should reject the probe before sending")

    provider = Provider()
    for name, value in settings.items():
        setattr(provider, name, value)
    capped = dynamic_probe.CappedProvider(provider)
    repository, definition = dynamic_probe.fixtures()
    result = dynamic_probe.DynamicAgent(repository, capped, definition).ask("审查采购数量")
    assert result["error"] == code
    assert result["usage"]["model_calls"] == capped.calls == 0


def test_probe_supports_providers_without_optional_capacity_settings():
    from backend.app.agent.policy import Budget

    class Provider:
        context_window = 32768

    budget = Budget.for_provider(dynamic_probe.CappedProvider(Provider()), "standard")
    assert budget.max_input is None and budget.max_output is None
    assert budget.prompt_limit(512) == 32768 - 512 - 1639
