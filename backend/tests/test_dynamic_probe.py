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
