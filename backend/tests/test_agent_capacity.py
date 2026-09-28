"""A complete review reports an impossible extraction budget before model calls."""

from dataclasses import replace
from pathlib import Path

from langchain_core.messages import AIMessage

from backend.app.agent.graph import AgentSession
from backend.app.evidence.repository import EvidenceRepository, load_case
from backend.app.parsing import EvidenceBlock

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


def test_full_review_reports_extraction_capacity_before_spending_model_calls():
    original = load_case(PACK)
    documents = []
    for document in original.documents:
        if document.role != "quote":
            documents.append(document)
            continue
        blocks = tuple(
            EvidenceBlock("", document.document_id, "采购数量" if column == "A" else "10 台",
                          sheet="报价", cell=f"{column}{row}")
            for row in range(1, 1001) for column in ("A", "B")
        )
        documents.append(replace(document, parsed=replace(document.parsed, blocks=blocks)))
    repo = EvidenceRepository(original.case_id, original.revision + 1, documents)

    class CountingProvider:
        model_id = "offline"
        context_window = 100000

        def __init__(self):
            self.calls = 0

        def decide(self, messages, tools, **kwargs):
            self.calls += 1
            return AIMessage(content="", tool_calls=[{"id": "clarify", "name": "request_clarification",
                                                      "args": {"question": "请确认范围。"}}])

    provider = CountingProvider()
    result = AgentSession(repo, provider, mode="thorough").ask(
        "请完整审查供应商 A 的八项指标")

    assert result.status == "partial"
    assert result.error == "review_capacity"
    assert "批次" in result.answer
    assert provider.calls == 0
