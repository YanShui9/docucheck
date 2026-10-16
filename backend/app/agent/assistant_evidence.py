"""Bounded source windows are read back from original text and persist their offsets."""
import re

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
    item_id: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def single_offset(self):
        if self.start_char and len(self.evidence_ids) != 1:
            raise ValueError("source_offset")
        return self


def hit_windows(retriever, hits, *, item_id=None):
    targets = []
    ends = {}
    for hit in hits:
        spans = hit.get("source_spans")
        if spans:
            for span in spans:
                if (span["evidence_id"] not in hit["evidence_refs"] or type(span["start"]) is not int or span["start"] < 0
                        or type(span["end"]) is not int or span["end"] <= span["start"]):
                    raise ValueError("retrieval_source_span")
                key = (span["evidence_id"], span["start"])
                targets.append(key)
                ends[key] = max(ends.get(key, 0), span["end"])
        else:
            targets.extend((ref, 0) for ref in hit["evidence_refs"])
    targets = list(dict.fromkeys(targets))[:12]
    if not targets:
        return []
    scope = {"item_id": item_id} if item_id is not None else {}
    if all(start == 0 for _, start in targets):
        rows = retriever.read_source([ref for ref, _ in targets], **scope)
    else:
        rows = [row for ref, start in targets for row in retriever.read_source([ref], start_char=start, **scope)]
    seen = set(targets)
    # Index chunks can exceed one source window. Follow only their declared
    # spans, within the existing 12-window observation bound; never scan a file.
    for row in rows:
        end = ends.get((row["evidence_id"], row["start_char"]), 0)
        next_char = row["next_char"]
        key = (row["evidence_id"], next_char)
        if next_char is not None and next_char < end and key not in seen and len(rows) < 12:
            seen.add(key)
            ends[key] = end
            rows.extend(retriever.read_source([row["evidence_id"]], start_char=next_char, **scope))
    return rows


def observed(row):
    return {key: row.get(key) for key in ("evidence_id", "text", "role", "supplier", "page_idx", "sheet", "cell", "truncated", "start_char", "next_char")}


def recorded(row):
    return {"evidence_id": row["evidence_id"], "start_char": row.get("start_char", 0), "text": row["text"]}


def supports(windows, evidence_id, quote):
    return any(w["evidence_id"] == evidence_id and quote in w["text"] for w in windows)


def citation_context(text, start, end):
    """Keep a complete cell/block, or complete sentences for a long block.

    Commas and semicolons do not form clipping boundaries: they can introduce
    qualifications. Boundaries come from the original, not the clipped read.
    """
    if len(text.encode("utf-8")) <= MAX_TEXT_BYTES:
        return 0, text
    boundaries = list(re.finditer(r"[。！？!?]+|\n\s*\n|\.(?=\s|$)", text))
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.end() for m in boundaries if m.end() >= end), default=len(text))
    context = text[left:right]
    if len(context.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("citation_context_limit")
    return left, context


def verify_windows(repo, payload, *, item_bindings=None, definition=None):
    windows = payload.get("source_windows")
    if payload.get('citation_offsets_version') == 1 and windows is None:
        raise ValueError('assistant_source_changed')
    if windows is None:  # Backward compatible with already persisted prefix-only answers.
        windows = [{"evidence_id": ref, "start_char": 0, "text": text} for ref, text in payload["source_reads"].items()]
    retrieval = payload.get("retrieval") or {}
    if retrieval.get("item_id") is not None:
        from ..criteria.item_bindings import review_bindings
        from ..criteria.models import ChecklistDefinition

        if item_bindings is None or definition is None:
            raise ValueError("assistant_source_changed")
        context = review_bindings(item_bindings, definition, repo)
        parsed = ChecklistDefinition.model_validate(definition) if isinstance(definition, dict) else definition
        allowed = context.bindings.allowed_refs(retrieval["item_id"])
        if (retrieval["item_id"] not in {i.item_id for i in parsed.items}
                or any(retrieval.get(k) != v for k, v in context.identity().items())
                or any(w["evidence_id"] not in allowed for w in windows)):
            raise ValueError("assistant_source_changed")
    latest = {}
    for window in windows:
        row = SourceWindow.model_validate(window)
        block = repo.read([row.evidence_id])[0]
        if row.start_char > len(block.text) or _clip(block.text[row.start_char:], MAX_TEXT_BYTES)[0] != row.text:
            raise ValueError("assistant_source_changed")
        latest[row.evidence_id] = row.text
    if latest != payload["source_reads"] or any(not supports(windows, c["evidence_id"], c["quote"]) for c in payload["citations"]):
        raise ValueError("assistant_source_changed")
    if payload.get('citation_offsets_version') == 1:
        for citation in payload['citations']:
            if not any(w['evidence_id'] == citation['evidence_id']
                and w['start_char'] <= citation['start_char']
                and citation['start_char'] + len(citation['quote']) <= w['start_char'] + len(w['text'])
                and w['text'][citation['start_char'] - w['start_char']:
                    citation['start_char'] - w['start_char'] + len(citation['quote'])] == citation['quote']
                for w in windows):
                raise ValueError('assistant_source_changed')
    if payload.get("citation_context_version") == 1:
        if payload.get("citation_offsets_version") != 1:
            raise ValueError("assistant_source_changed")
        for citation in payload["citations"]:
            text = repo.read([citation["evidence_id"]])[0].text
            try:
                start, context = citation_context(text, citation["start_char"],
                    citation["start_char"] + len(citation["quote"]))
            except ValueError as exc:
                raise ValueError("assistant_source_changed") from exc
            if start != citation["start_char"] or context != citation["quote"]:
                raise ValueError("assistant_source_changed")
