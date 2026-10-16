from pathlib import Path

import pytest

from backend.app.evidence.repository import load_case
from backend.app.evidence.retrieval import Retriever

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


def sheet_repository(rows):
    from backend.app.evidence.models import Document
    from backend.app.evidence.repository import EvidenceRepository
    from backend.app.parsing import EvidenceBlock, ParseResult

    blocks = tuple(EvidenceBlock(f"{sheet}-{col}{row}", "table.xlsx", f"{sheet}{col}{row}",
                                sheet=sheet, cell=f"{col}{row}")
                   for sheet in ("报价", "附件") for row in range(1, rows + 1) for col in ("A", "B"))
    parsed = ParseResult("table.xlsx", "test", "complete", blocks,
                         total_sheets=2, processed_sheets=("报价", "附件"))
    return EvidenceRepository("case", 1, [Document("table.xlsx", "hash", "quote", "vendor-a", None, parsed)])


@pytest.mark.parametrize("rows", [10, 30])
def test_xlsx_chunking_scans_authorized_blocks_a_constant_number_of_times(rows):
    class Counted(list):
        scans = 0

        def __iter__(self):
            self.scans += 1
            return super().__iter__()

    repo = sheet_repository(rows)
    authorized = Counted(repo.blocks)
    chunks = Retriever(repo)._chunks(authorized)
    assert len(chunks) == rows * 2
    assert authorized.scans <= 3


def test_xlsx_chunk_headers_preserve_source_order_offsets_and_authorized_subset():
    repo = sheet_repository(2)
    # Only B1 and row 2 of one sheet are authorized. A1/other-sheet text must not leak.
    blocks = [b for b in repo.blocks if b.sheet == "报价" and b.cell != "A1"]
    chunks = Retriever(repo)._chunks(blocks)
    assert [chunk.text for chunk in chunks] == ["报价B1", "报价B1\n报价A2\n报价B2"]
    assert list(chunks[1].evidence_refs) == [b.evidence_id for b in blocks]
    assert [(s.evidence_id, s.start, s.end) for s in chunks[1].spans] == [
        (b.evidence_id, 0, len(b.text)) for b in blocks]


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


@pytest.mark.parametrize("query", ["存储", "SSD", "ssd", "硬盘", "存照片和文件的硬盘有多大？"])
def test_storage_alias_recalls_original_ssd_source_without_other_supplier(query):
    from dataclasses import replace

    from backend.app.evidence.repository import EvidenceRepository

    original = load_case(PACK)
    spec = next(document for document in original.documents if document.role == "spec")
    foreign = replace(spec, document_id="foreign-spec", supplier="vendor-b",
                      parsed=replace(spec.parsed, document_id="foreign-spec"))
    repo = EvidenceRepository(original.case_id, original.revision + 1,
                              [*original.documents, foreign])
    refs = {ref for hit in Retriever(repo).search(query, supplier="vendor-a")
            for ref in hit["evidence_refs"]}
    expected = {block.evidence_id for block in repo.blocks
                if block.supplier in {None, "vendor-a"}
                and ("标称存储" in block.text or "标称 SSD" in block.text)}
    assert len(expected) == 2
    assert expected <= refs
    assert all(block.supplier in {None, "vendor-a"} for block in repo.read(list(refs)))


def test_hard_disk_question_recalls_actual_ssd_capacity_and_source_location():
    repo = load_case(PACK)
    retriever = Retriever(repo)
    hits = retriever.search("存照片和文件的硬盘有多大？", supplier="vendor-a", limit=6)
    storage = next(block for block in repo.blocks
                   if block.role == "spec" and block.text == "标称 SSD 1 TB")
    assert hits and storage.evidence_id in hits[0]["evidence_refs"]
    source = retriever.read_source([storage.evidence_id])[0]
    assert source["text"] == "标称 SSD 1 TB"
    assert source["document_id"] == "vendor-a-spec.pdf"
    assert source["role"] == "spec" and source["supplier"] == "vendor-a"
    assert source["page_idx"] == 0 and source["bbox"] == storage.bbox
    assert source["source_hash"] == storage.source_hash
    refs = {ref for hit in hits for ref in hit["evidence_refs"]}
    assert any(block.role == "requirement" and "512 GB" in block.text
               for block in repo.read(list(refs)))


@pytest.mark.parametrize("query, label, count", [("硬盘", "storage", 2), ("内存", "memory", 3)])
def test_hard_disk_and_memory_queries_keep_their_original_sources_distinct(query, label, count):
    repo = load_case(PACK)
    refs = {ref for hit in Retriever(repo).search(query, supplier="vendor-a")
            for ref in hit["evidence_refs"]}
    storage = {block.evidence_id for block in repo.blocks
               if "标称存储" in block.text or "标称 SSD" in block.text}
    memory = {block.evidence_id for block in repo.blocks
              if "已装内存" in block.text and "GB" in block.text}
    expected, excluded = (storage, memory) if label == "storage" else (memory, storage)
    assert len(expected) == count and expected <= refs
    assert not refs.intersection(excluded)


@pytest.mark.parametrize("query", ["供应商答应免费维修几年？", "这台电脑能防水吗？"])
def test_no_answer_questions_do_not_gain_storage_sources_from_hard_disk_alias(query):
    repo = load_case(PACK)
    refs = {ref for hit in Retriever(repo).search(query, supplier="vendor-a", limit=6)
            for ref in hit["evidence_refs"]}
    assert all("标称 SSD" not in block.text and "标称存储" not in block.text
               for block in repo.read(list(refs)))


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
