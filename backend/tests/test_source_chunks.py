from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.evidence.chunks import ChunkPolicy, chunk_snapshot, restore_text
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, load_case
from backend.app.parsing import EvidenceBlock, ParseResult

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


def long_repository():
    text = "采购食品需提供批次检测报告，交付期为合同签订后十个工作日。" * 50
    parsed = ParseResult("spec.pdf", "test-parser", "complete",
                         (EvidenceBlock("raw", "spec.pdf", text, page_idx=0),),
                         total_pages=1, processed_pages=(0,))
    return EvidenceRepository("case", 1, [Document("spec.pdf", "hash", "spec",
                              "vendor-a", None, parsed)])


def test_offsets_cover_unicode_source_with_overlap_and_stable_ids():
    repo = long_repository()
    chunks = chunk_snapshot(repo, "project", "model-revision", dimension=3)
    assert len(chunks) > 2
    assert chunks == chunk_snapshot(repo, "project", "model-revision", dimension=3)
    covered = set()
    for chunk in chunks:
        assert len(chunk.chunk_id) == 64 and len(chunk.text.encode()) <= 1600
        assert restore_text(chunk, repo) == chunk.text
        assert chunk.item_ids == ()
        covered.update(range(chunk.spans[0].start, chunk.spans[0].end))
    assert covered == set(range(len(repo.blocks[0].text)))
    assert chunks[1].spans[0].start < chunks[0].spans[0].end
    for project, revision, dimension in [("other", "model-revision", 3),
                                         ("project", "updated-model", 3),
                                         ("project", "model-revision", 4)]:
        changed = chunk_snapshot(repo, project, revision, dimension=dimension)
        assert {c.chunk_id for c in changed}.isdisjoint(c.chunk_id for c in chunks)


def test_table_row_preserves_name_value_and_only_same_document_context():
    repo = load_case(PACK)
    chunks = chunk_snapshot(repo, "project", "revision")
    cell = next(b for b in repo.blocks if b.cell == "B7")
    chunk = next(c for c in chunks if cell.evidence_id in c.evidence_refs)
    assert "采购数量" in chunk.text and "10 台" in chunk.text
    assert all(b.document_id == cell.document_id for b in repo.read(list(chunk.evidence_refs)))
    assert all(restore_text(c, repo) == c.text for c in chunks)
    changed = replace(chunk, text="模型编造的文本")
    with pytest.raises(ValueError, match="chunk_source_mismatch"):
        restore_text(changed, repo)


@pytest.mark.parametrize("kwargs", [{"max_bytes": 1}, {"overlap_bytes": 1600},
                                    {"overlap_bytes": -1}, {"max_bytes": True}])
def test_invalid_chunk_budget_is_rejected(kwargs):
    with pytest.raises(ValueError, match="chunk_policy"):
        ChunkPolicy(**kwargs)


def test_sheet_context_is_precomputed_instead_of_scanning_all_cells_per_row():
    class CountingBlocks(tuple):
        scans = 0

        def __iter__(self):
            self.scans += 1
            return super().__iter__()

    blocks = tuple(EvidenceBlock(f"raw-{row}-{col}", "quote.xlsx", f"项目 {row} {col}",
                                sheet="报价", cell=f"{col}{row}")
                   for row in range(1, 81) for col in "ABCD")
    parsed = ParseResult("quote.xlsx", "parser", "complete", blocks)
    repo = EvidenceRepository("case", 1, [Document("quote.xlsx", "hash", "quote", "vendor-a", None, parsed)])
    counted = CountingBlocks(repo.blocks)
    repo.blocks = counted
    chunks = chunk_snapshot(repo, "project", "revision")
    assert len(chunks) == 80 and counted.scans <= 3
