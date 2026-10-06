import json

from backend.app import retrieval_demo


def test_default_preview_reads_verified_fixture_without_network(capsys, monkeypatch):
    monkeypatch.setattr(retrieval_demo, "OllamaEmbedding", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("preview must not create model client")))
    assert retrieval_demo.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "preview" and result["network_calls"] == 0
    assert result["chunks"] > 0 and result["dimension"] == 1024
    assert result["chat_model_calls"] == result["embedding_requests"] == 0


def test_invalid_question_is_rejected_before_network(capsys):
    assert retrieval_demo.main(["--question", " ", "--send"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "retrieval_query"
