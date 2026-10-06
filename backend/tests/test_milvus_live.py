"""Opt-in local Milvus integration; no embedding/model or procurement documents."""
import logging
import os
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr

from backend.app.vector_store.config import MilvusConfig
from backend.app.vector_store.probe import _sdk_client, run_probe

pytestmark = pytest.mark.skipif(
    os.getenv("DOCUCHECK_RUN_MILVUS_TESTS") != "1",
    reason="explicit DOCUCHECK_RUN_MILVUS_TESTS=1 required",
)


@pytest.fixture(scope="module")
def config():
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env", interpolate=False)
    result = MilvusConfig.from_env({key: value for key, value in env.items() if value is not None})
    assert result.database == "docucheck_probe"
    assert result.uri == "http://127.0.0.1:19530"
    assert result.token.get_secret_value().startswith("docucheck_probe:")
    return result


@pytest.fixture(autouse=True)
def silence_sdk_logging():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


@contextmanager
def connected(config):
    client = _sdk_client(config)
    try:
        yield client
    finally:
        client.close()


def test_synthetic_vectors_and_cleanup(config):
    result = run_probe(config)
    assert result["status"] == "completed"
    assert result["matched_ids"] == [1, 2] and result["remaining_ids"] == [1]
    assert result["synthetic_vectors"] == 5 and result["collection_removed"]
    with connected(config) as client:
        assert not client.has_collection(result["collection"], timeout=10)


def test_probe_account_cannot_read_foreign_database_or_admin_users(config):
    import grpc
    from pymilvus import MilvusException

    def denied(operation):
        with pytest.raises((grpc.RpcError, MilvusException)) as failure:
            operation()
        error = failure.value
        if isinstance(error, grpc.RpcError):
            assert error.code() == grpc.StatusCode.PERMISSION_DENIED
        else:
            assert "permission" in error.message.lower() or "privilege" in error.message.lower()

    with connected(config) as client:
        denied(lambda: client.list_users(timeout=10))
    values = dotenv_values(Path(__file__).resolve().parents[2] / ".env", interpolate=False)
    root_config = replace(config, database="default",
                          token=SecretStr("root:" + values["DOCUCHECK_MILVUS_ROOT_PASSWORD"]))
    collection = "docucheck_foreign_probe_" + uuid4().hex
    with connected(root_config) as root:
        assert not root.has_collection(collection, timeout=10)
        root.create_collection(collection, dimension=8, consistency_level="Strong", timeout=10)
        try:
            root.upsert(collection, data=[{"id": 1, "vector": [1.0] + [0.0] * 7}], timeout=10)
            with connected(replace(config, database="default")) as foreign:
                assert collection not in foreign.list_collections(timeout=10)
                denied(lambda: foreign.describe_collection(collection, timeout=10))
                denied(lambda: foreign.query(collection, filter="id > 0", timeout=10))
        finally:
            root.drop_collection(collection, timeout=10)
        assert not root.has_collection(collection, timeout=10)


def test_anonymous_and_old_default_root_rejected(config):
    import requests

    for headers in ({}, {"Authorization": "Bearer root:Milvus"}):
        response = requests.post(config.uri + "/v2/vectordb/collections/list",
                                 headers=headers, json={"dbName": "default"}, timeout=10)
        assert response.json()["code"] == 1800
