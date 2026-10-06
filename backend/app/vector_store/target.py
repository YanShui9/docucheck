"""Pinned application index target; reading settings never contacts a model."""
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..evidence.chunks import ChunkPolicy
from ..evidence.repository import digest

STORE_SCHEMA = "original-chunks-v2"


@dataclass(frozen=True)
class IndexTarget:
    revision: str
    dimension: int = 1024
    policy: ChunkPolicy = field(default_factory=ChunkPolicy)

    def __post_init__(self):
        if (not isinstance(self.revision, str) or not re.fullmatch(
                r"ollama-bge-m3-[a-f0-9]{64}-normalized-v1", self.revision)
                or type(self.dimension) is not int or not 2 <= self.dimension <= 32768):
            raise ValueError("index_target_configuration")

    @property
    def identity(self) -> dict[str, str | int]:
        return {"embedding_revision": self.revision, "dimension": self.dimension,
                "chunk_schema": self.policy.revision, "store_schema": STORE_SCHEMA}

    @property
    def key(self) -> str:
        return digest(self.identity)

    @property
    def collection(self) -> str:
        return "docucheck_rag_" + self.key[:32]

    @classmethod
    def from_manifest(cls, row: Mapping[str, Any]) -> "IndexTarget":
        match = re.fullmatch(r"source-offsets-v1-(\d+)-(\d+)", row["chunk_schema"])
        if not match:
            raise ValueError("index_target_configuration")
        target = cls(row["embedding_revision"], row["dimension"], ChunkPolicy(*map(int, match.groups())))
        if target.collection != row["collection_name"]:
            raise ValueError("index_target_configuration")
        return target


def configured_target() -> IndexTarget | None:
    enabled = os.getenv("DOCUCHECK_VECTOR_RETRIEVAL_ENABLED", "0")
    if enabled not in {"0", "1"}:
        raise ValueError("index_target_configuration")
    if enabled == "0":
        return None
    return IndexTarget(os.getenv("DOCUCHECK_INDEX_EMBEDDING_REVISION", ""))
