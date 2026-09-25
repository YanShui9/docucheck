"""Immutable input snapshots owned by the server."""
from dataclasses import dataclass
from typing import Literal

from ..parsing import EvidenceBlock, ParseResult


@dataclass(frozen=True)
class Document:
    document_id: str
    source_hash: str
    role: Literal["requirement", "quote", "spec"]
    supplier: str | None
    model: str | None
    parsed: ParseResult


@dataclass(frozen=True)
class ScopedEvidence(EvidenceBlock):
    role: str = ""
    supplier: str | None = None
    model: str | None = None
    source_hash: str = ""
    parser_version: str = ""
    ordinal: int = 0
