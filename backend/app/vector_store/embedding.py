"""Local BGE-M3 adapter, including Docker Desktop's explicit host bridge."""
import math
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


class EmbeddingError(RuntimeError):
    pass


def validate_vectors(vectors, *, count: int, dimension: int) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != count:
        raise EmbeddingError("embedding_invalid_vectors")
    result = []
    for vector in vectors:
        if (not isinstance(vector, list) or len(vector) != dimension
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
            raise EmbeddingError("embedding_invalid_vectors")
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise EmbeddingError("embedding_invalid_vectors")
        result.append([float(value / norm) for value in vector])
    return result


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "bge-m3:latest"
    timeout: float = 60

    def __post_init__(self):
        try:
            url = urlsplit(self.base_url)
            if (url.scheme != "http" or url.hostname not in {
                    "127.0.0.1", "localhost", "::1", "host.docker.internal"}
                    or url.username or url.password or url.path not in {"", "/"}
                    or url.query or url.fragment or self.model != "bge-m3:latest"
                    or (url.port is not None and not 1 <= url.port <= 65535)
                    or not math.isfinite(float(self.timeout)) or not 0 < float(self.timeout) <= 120):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError("embedding_configuration") from None

    @classmethod
    def from_env(cls):
        try:
            return cls(os.getenv("DOCUCHECK_EMBEDDING_BASE_URL", "http://127.0.0.1:11434"),
                       os.getenv("DOCUCHECK_EMBEDDING_MODEL", "bge-m3:latest"),
                       float(os.getenv("DOCUCHECK_EMBEDDING_TIMEOUT", "60")))
        except ValueError:
            raise ValueError("embedding_configuration") from None


class OllamaEmbedding:
    dimension = 1024

    def __init__(self, config: OllamaConfig, *, client: httpx.Client | None = None):
        self.config = config
        self.revision: str | None = None
        self._digest: str | None = None
        self._owns_client = client is None
        self.embedding_requests = 0
        self.client = client or httpx.Client(base_url=config.base_url, timeout=config.timeout,
                                           trust_env=False, follow_redirects=False)

    def _request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, timeout=self.config.timeout, **kwargs)
            response.raise_for_status()
            # Bound decoded output before accepting it into application state.
            if len(response.content) > 4_000_000:
                raise ValueError
            return response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            raise EmbeddingError("embedding_service_unavailable") from None

    def _model_digest(self):
        body = self._request("GET", "/api/tags")
        if not isinstance(body, dict) or not isinstance(body.get("models"), list):
            raise EmbeddingError("embedding_invalid_model_metadata")
        matches = [m for m in body["models"] if isinstance(m, dict)
                   and m.get("name") == self.config.model]
        if len(matches) != 1:
            raise EmbeddingError("embedding_model_not_installed")
        value = matches[0].get("digest", "")
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise EmbeddingError("embedding_invalid_model_metadata")
        return value

    def resolve(self):
        value = self._model_digest()
        if self._digest is not None and self._digest != value:
            raise EmbeddingError("embedding_model_changed")
        self._digest = value
        self.revision = f"ollama-bge-m3-{value}-normalized-v1"
        return self.revision

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.revision is None:
            raise EmbeddingError("embedding_not_resolved")
        if (not isinstance(texts, list) or not 1 <= len(texts) <= 8
                or any(not isinstance(t, str) or not t.strip() or len(t.encode()) > 8192 for t in texts)):
            raise EmbeddingError("embedding_input")
        if self._model_digest() != self._digest:
            raise EmbeddingError("embedding_model_changed")
        self.embedding_requests += 1
        body = self._request("POST", "/api/embed", json={"model": self.config.model,
                             "input": texts, "truncate": False, "keep_alive": "1m"})
        if not isinstance(body, dict) or body.get("model") != self.config.model:
            raise EmbeddingError("embedding_invalid_model_metadata")
        vectors = validate_vectors(body.get("embeddings"), count=len(texts), dimension=self.dimension)
        if self._model_digest() != self._digest:
            raise EmbeddingError("embedding_model_changed")
        return vectors

    def close(self):
        if self._owns_client:
            self.client.close()
