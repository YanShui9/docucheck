"""Bounded source windows are read back from original text and persist their offsets."""
from pydantic import Field, model_validator

from ..criteria.models import Contract
from ..evidence.retrieval import MAX_TEXT_BYTES, _clip


class SourceWindow(Contract):
    evidence_id: str = Field(min_length=1, max_length=64)
    start_char: int = Field(ge=0)
    text: str = Field(max_length=MAX_TEXT_BYTES)


class ReadSourceArgs(Contract):
    evidence_ids: list[str] = Field(min_length=1, max_length=4)
    start_char: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def single_offset(self):
        if self.start_char and len(self.evidence_ids) != 1:
            raise ValueError("source_offset")
        return self


def hit_windows(retriever, hits):
    targets = []
    for hit in hits:
        spans = hit.get("source_spans")
        if spans:
            for span in spans:
                if span["evidence_id"] not in hit["evidence_refs"] or type(span["start"]) is not int or span["start"] < 0:
                    raise ValueError("retrieval_source_span")
                targets.append((span["evidence_id"], span["start"]))
        else:
            targets.extend((ref, 0) for ref in hit["evidence_refs"])
    targets = list(dict.fromkeys(targets))[:12]
    if not targets:
        return []
    if all(start == 0 for _, start in targets):
        return retriever.read_source([ref for ref, _ in targets])
    return [row for ref, start in targets for row in retriever.read_source([ref], start_char=start)]


def observed(row):
    return {key: row.get(key) for key in ("evidence_id", "text", "role", "supplier", "page_idx", "sheet", "cell", "truncated", "start_char", "next_char")}


def recorded(row):
    return {"evidence_id": row["evidence_id"], "start_char": row.get("start_char", 0), "text": row["text"]}


def supports(windows, evidence_id, quote):
    return any(w["evidence_id"] == evidence_id and quote in w["text"] for w in windows)


def verify_windows(repo, payload):
    windows = payload.get("source_windows")
    if windows is None:  # Backward compatible with already persisted prefix-only answers.
        windows = [{"evidence_id": ref, "start_char": 0, "text": text} for ref, text in payload["source_reads"].items()]
    latest = {}
    for window in windows:
        row = SourceWindow.model_validate(window)
        block = repo.read([row.evidence_id])[0]
        if row.start_char > len(block.text) or _clip(block.text[row.start_char:], MAX_TEXT_BYTES)[0] != row.text:
            raise ValueError("assistant_source_changed")
        latest[row.evidence_id] = row.text
    if latest != payload["source_reads"] or any(not supports(windows, c["evidence_id"], c["quote"]) for c in payload["citations"]):
        raise ValueError("assistant_source_changed")
