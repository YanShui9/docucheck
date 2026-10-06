import json

import httpx
import pytest

from backend.app.vector_store.embedding import (
    EmbeddingError,
    OllamaConfig,
    OllamaEmbedding,
    validate_vectors,
)

DIGEST = "a" * 64


def provider(handler):
    return OllamaEmbedding(OllamaConfig(), client=httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:11434"))


def test_local_embed_binds_digest_and_disables_silent_truncation():
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "bge-m3:latest", "digest": DIGEST}]})
        body = json.loads(request.content)
        assert body["truncate"] is False and body["input"] == ["什么时候能送到？"]
        assert body["keep_alive"] == "1m"
        return httpx.Response(200, json={"model": "bge-m3:latest",
                                       "embeddings": [[1.0] + [0.0] * 1023]})

    model = provider(handle)
    model.resolve()
    assert DIGEST in model.revision and model.dimension == 1024
    assert len(model.embed(["什么时候能送到？"])[0]) == 1024
    assert calls == ["/api/tags", "/api/tags", "/api/embed", "/api/tags"]


def test_changed_model_cannot_reuse_vectors_or_send_query():
    digest = DIGEST
    model = provider(lambda request: httpx.Response(200, json={
        "models": [{"name": "bge-m3:latest", "digest": digest}]}))
    model.resolve()
    digest = "b" * 64
    with pytest.raises(EmbeddingError, match="embedding_model_changed"):
        model.embed(["保质期"])


@pytest.mark.parametrize("uri", ["https://outside.example", "http://user:password@localhost",
                                "http://localhost/path", "http://localhost?key=secret"])
def test_local_embedding_rejects_remote_or_credential_url(uri):
    with pytest.raises(ValueError, match="embedding_configuration"):
        OllamaConfig(base_url=uri)


def test_docker_worker_can_address_local_host_ollama_without_arbitrary_remote_hosts():
    config = OllamaConfig(base_url="http://host.docker.internal:11434")
    assert config.base_url == "http://host.docker.internal:11434"
    for url in ("http://host.docker.internal.example:11434", "http://192.0.2.1:11434",
                "http://user:password@host.docker.internal:11434",
                "http://host.docker.internal:11434/api/embed"):
        with pytest.raises(ValueError, match="embedding_configuration"):
            OllamaConfig(base_url=url)


@pytest.mark.parametrize("vectors", [[[0, 0, 0]], [[float("nan"), 1, 0]],
                                     [[True, 0, 1]], [[1, 0]], [], [["1", 0, 1]]])
def test_invalid_vectors_rejected(vectors):
    with pytest.raises(EmbeddingError, match="embedding_invalid_vectors"):
        validate_vectors(vectors, count=1, dimension=3)


def test_external_error_is_sanitized_and_request_not_echoed():
    model = provider(lambda request: httpx.Response(500, text="private procurement text"))
    with pytest.raises(EmbeddingError) as exc:
        model.resolve()
    assert str(exc.value) == "embedding_service_unavailable"


def test_embedding_does_not_autodownload_and_empty_or_large_inputs_fail():
    model = provider(lambda request: pytest.fail("network must not be used"))
    with pytest.raises(EmbeddingError, match="embedding_not_resolved"):
        model.embed(["合同"])
    model.revision = "test"
    for texts in ([], [""], ["中" * 9000], ["x"] * 9):
        with pytest.raises(EmbeddingError, match="embedding_input"):
            model.embed(texts)
