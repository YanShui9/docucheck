from pathlib import Path

import pytest

from backend.app.evidence.repository import load_case
from backend.app.evidence.retrieval import Retriever

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


def test_alias_retrieval_recalls_requirement_and_both_conflicting_sources():
    repo = load_case(PACK)
    retriever = Retriever(repo)
    matches = retriever.search("RAM", supplier="vendor-a", limit=12)
    refs = {ref for hit in matches for ref in hit["evidence_refs"]}
    memory = [b for b in repo.blocks if "已装内存" in b.text and "GB" in b.text]
    assert len(memory) == 3
    assert {b.role for b in memory} == {"requirement", "quote", "spec"}
    assert {b.evidence_id for b in memory} <= refs
    assert any(
        b.role == "requirement" and b.page_idx == 0 and "16 GB" in b.text
        for b in memory
    )
    assert any(
        b.role == "quote"
        and b.sheet == "报价"
        and b.cell == "B6"
        and "16 GB" in b.text
        for b in memory
    )
    assert any(
        b.role == "spec" and b.page_idx == 0 and "32 GB" in b.text
        for b in memory
    )
    assert all(hit["text"] and hit["evidence_refs"] for hit in matches)
    assert retriever.search("completely-nonexistent-xyz") == []
    with pytest.raises(ValueError, match="supplier_scope"):
        retriever.search("内存", supplier="foreign")


def test_read_source_preserves_cells_and_neighbors_without_invented_locations():
    repo = load_case(PACK)
    retriever = Retriever(repo)
    cell = next(b for b in repo.blocks if b.cell == "B6")
    results = retriever.read_source([cell.evidence_id], neighbors=1)
    assert {b["cell"] for b in results if b["sheet"] == "报价"} >= {"A6", "B6"}
    assert all(b["evidence_id"] in {x.evidence_id for x in repo.blocks} for b in results)
    with pytest.raises(ValueError, match="evidence_scope"):
        retriever.read_source([cell.evidence_id, "foreign-evidence"], neighbors=1)


def test_long_search_hit_can_be_read_to_the_end_via_tool_offsets():
    from backend.app.agent.tools import ToolExecutor
    from backend.app.evidence.models import Document
    from backend.app.evidence.repository import EvidenceRepository
    from backend.app.parsing import EvidenceBlock, ParseResult

    original = "开头" * 500 + " tailmarker32"
    parsed = ParseResult(
        document_id="long-spec.pdf",
        parser_version="test-parser",
        status="complete",
        blocks=(EvidenceBlock("raw-id", "long-spec.pdf", original, page_idx=0),),
        total_pages=1,
        processed_pages=(0,),
    )
    repo = EvidenceRepository(
        "case-long",
        1,
        [
            Document(
                "long-spec.pdf", "source-hash", "spec", "vendor-a", None, parsed
            )
        ],
    )
    evidence_id = repo.blocks[0].evidence_id
    matches = Retriever(repo).search("tailmarker32", supplier="vendor-a")
    assert any(
        "tailmarker32" in hit["text"] and evidence_id in hit["evidence_refs"]
        for hit in matches
    )

    tool = ToolExecutor(repo)
    parts = []
    cursor = 0
    for _ in range(10):
        item = tool.execute(
            "read_source", {"evidence_ids": [evidence_id], "start_char": cursor}, None
        )["data"][0]
        assert item["start_char"] == cursor
        parts.append(item["text"])
        if item["next_char"] is None:
            break
        assert item["truncated"]
        cursor = item["next_char"]
    else:
        pytest.fail("source pagination did not finish")
    assert "".join(parts) == original
    with pytest.raises(ValueError, match="source_offset"):
        Retriever(repo).read_source([evidence_id], start_char=len(original))
    with pytest.raises(ValueError, match="source_offset"):
        Retriever(repo).read_source([evidence_id, evidence_id], start_char=1)
