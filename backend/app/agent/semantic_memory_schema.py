"""Finite auxiliary notes; source IDs are handles, never procurement evidence."""

import json
from typing import Literal

from pydantic import Field

from ..criteria.models import Contract


class MemoryNote(Contract):
    kind: Literal["goal", "decision", "open_question", "next_step"]
    text: str = Field(min_length=1, max_length=300)
    source_message_ids: list[str] = Field(min_length=1, max_length=2)


class SummaryOutput(Contract):
    notes: list[MemoryNote] = Field(min_length=1, max_length=8)


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def checked_notes(notes, sources):
    result = SummaryOutput.model_validate({"notes": notes}).model_dump(mode="json")["notes"]
    ids = {source["message_id"] for source in sources}
    if (encoded_size({"notes": result}) > 3500
            or any(not note["text"].strip() or len(set(note["source_message_ids"])) != len(note["source_message_ids"])
                   or not set(note["source_message_ids"]) <= ids for note in result)):
        raise ValueError("invalid_semantic_summary")
    return result
