"""The Step 5 probe is bounded and safe to preview without model access."""

import json

from langchain_core.messages import AIMessage


def test_preview_counts_real_batches_without_loading_provider(monkeypatch, capsys):
    from backend.app import memory_extract_probe

    def forbidden():
        raise AssertionError("preview_must_not_load_provider")

    monkeypatch.setattr(memory_extract_probe, "configured_provider", forbidden)
    assert memory_extract_probe.main([]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview == {"mode": "preview", "supplier": "vendor-a",
                       "model_requests": 3, "evidence_blocks": 35}


def test_send_uses_three_model_requests_and_checks_conflict(monkeypatch, capsys):
    from backend.app import memory_extract_probe

    class ScriptedProvider:
        def __init__(self):
            self.calls = []

        def extract(self, messages, schema, *, timeout, max_output_tokens):
            self.calls.append((messages, schema, timeout, max_output_tokens))
            facts = []
            for item in json.loads(messages[-1].content)["evidence"]:
                if "已装内存" not in item["text"] or "GB" not in item["text"]:
                    continue
                facts.append({"raw_value": "32" if "32" in item["text"] else "16",
                              "raw_unit": "GB", "qualifier": "installed",
                              "comparator": "gte" if "至少" in item["text"] else "eq",
                              "evidence_refs": [item["evidence_id"]]})
            return AIMessage(content="", tool_calls=[{"name": "MemoryCandidates",
                "args": {"facts": facts}, "id": f"batch-{len(self.calls)}"}],
                usage_metadata={"input_tokens": 100, "output_tokens": 30,
                                "total_tokens": 130})

    fake = ScriptedProvider()
    monkeypatch.setattr(memory_extract_probe, "configured_provider", lambda: fake)
    monkeypatch.setattr(memory_extract_probe, "load_dotenv", lambda *args, **kwargs: None)
    assert memory_extract_probe.main(["--send"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(fake.calls) == 3
    assert all(timeout == 30 and limit == 512 for _, _, timeout, limit in fake.calls)
    assert result["model_requests"] == 3
    assert result["input_tokens"] == 300
    assert result["output_tokens"] == 90
    assert result["processing_status"] == "complete"
    assert result["business_status"] == "资料冲突"
    assert {(fact["role"], fact["value"]) for fact in result["facts"]} == {
        ("requirement", "16"), ("quote", "16"), ("spec", "32")}
    assert {fact["role"]: fact["location"] for fact in result["facts"]} == {
        "requirement": "PDF page 1", "quote": "报价!B6", "spec": "PDF page 1"}
