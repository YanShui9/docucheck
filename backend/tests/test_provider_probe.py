"""The user-run capability probe sends one synthetic sentence and one model request."""

from langchain_core.messages import AIMessage


def test_probe_exercises_forced_tool_call_with_synthetic_sentence(monkeypatch, capsys):
    from backend.app import provider_probe
    from backend.app.providers import FakeProvider

    fake = FakeProvider(
        extract_responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "MemoryProbe", "args": {"memory_gb": 16}, "id": "probe-1"}
                ],
                usage_metadata={
                    "input_tokens": 21,
                    "output_tokens": 8,
                    "total_tokens": 29,
                },
            )
        ]
    )
    monkeypatch.setattr(provider_probe, "configured_provider", lambda: fake)

    assert provider_probe.main() == 0
    assert len(fake.calls) == 1
    assert fake.calls[0]["operation"] == "extract"
    assert "模拟句子：已装内存 16 GB" in fake.calls[0]["messages"][-1].content
    assert '"tool_call": "MemoryProbe"' in capsys.readouterr().out


def test_probe_rejects_unconfigured_service_without_call(monkeypatch, capsys):
    from backend.app import provider_probe
    from backend.app.providers import ProviderError

    def missing():
        raise ProviderError("configuration_error")

    monkeypatch.setattr(provider_probe, "configured_provider", missing)
    assert provider_probe.main() == 2
    assert "configuration_error" in capsys.readouterr().err
