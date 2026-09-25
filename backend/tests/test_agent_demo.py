import json
from importlib import import_module
from pathlib import Path

from backend.tests.test_agent_graph import ScenarioProvider

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


def test_cli_two_turns_write_verifiable_local_traces(tmp_path, monkeypatch, capsys):
    demo = import_module("backend.app.agent_demo")
    provider = ScenarioProvider()
    monkeypatch.setattr(demo, "configured_provider", lambda: provider)
    answers = iter(["供应商 A 内存是否达标？", "根据哪两处判断？", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    code = demo.main(["--case-dir", str(PACK), "--provider", "configured", "--interactive",
                      "--output", str(tmp_path)])
    assert code == 0
    traces = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.glob("*.json")]
    assert len(traces) == 2
    assert all(t["status"] == "completed" for t in traces)
    assert len({t["context"]["conversation_id"] for t in traces}) == 1
    assert "资料冲突" in capsys.readouterr().out
    assert all("messages" not in str(t["events"]) for t in traces)


def test_missing_provider_config_fails_without_fake_fallback(tmp_path, monkeypatch, capsys):
    demo = import_module("backend.app.agent_demo")
    from backend.app.providers import ProviderError
    def missing():
        raise ProviderError("configuration_error")
    monkeypatch.setattr(demo, "configured_provider", missing)
    assert demo.main(["--case-dir", str(PACK), "--question", "内存？", "--output", str(tmp_path)]) == 2
    assert "configuration_error" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_plain_exit_ends_interactive_session_without_model_call(tmp_path, monkeypatch):
    demo = import_module("backend.app.agent_demo")
    provider = ScenarioProvider()
    answers = iter(["exit", "/exit"])
    monkeypatch.setattr(demo, "configured_provider", lambda: provider)
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    assert demo.main(["--interactive", "--output", str(tmp_path)]) == 0
    assert provider.steps == 0
    assert list(tmp_path.iterdir()) == []
