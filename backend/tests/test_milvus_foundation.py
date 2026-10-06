"""Milvus boundary contracts, no SDK, service or embedding/model request."""
import re

import pytest

from backend.app.milvus_probe import main
from backend.app.vector_store.config import MilvusConfig, MilvusConfigurationError
from backend.app.vector_store.probe import MilvusProbeError, preview, run_probe


def configured(**values):
    return MilvusConfig.from_env({"DOCUCHECK_MILVUS_TOKEN": "private-test-secret", **values})


class FakeClient:
    def __init__(self, *, existing=False, fail_search=False, fail_cleanup=False,
                 ignore_scope=False):
        self.existing, self.fail_search = existing, fail_search
        self.fail_cleanup, self.ignore_scope = fail_cleanup, ignore_scope
        self.rows, self.calls = [], []
        self.created = self.dropped = self.closed = False

    def has_collection(self, **kwargs):
        return self.existing

    def create_collection(self, **kwargs):
        self.created = True
        self.calls.append(("create", kwargs))

    def upsert(self, **kwargs):
        self.rows = kwargs["data"]
        self.calls.append(("upsert", kwargs))

    def selected(self, expression):
        constraints = re.findall(r'([a-z_]+) == "([^"]+)"', expression)
        return [row for row in self.rows if self.ignore_scope or
                all(row.get(key) == value for key, value in constraints)]

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        if self.fail_search:
            raise RuntimeError("SDK leaked private-test-secret")
        return [[{"id": row["id"], "entity": {
            key: row[key] for key in kwargs["output_fields"]
        }} for row in self.selected(kwargs["filter"])]]

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return [{"id": row["id"]} for row in self.selected(kwargs["filter"])]

    def delete(self, **kwargs):
        self.rows = [row for row in self.rows if row["id"] not in kwargs["ids"]]

    def drop_collection(self, **kwargs):
        if self.fail_cleanup:
            raise RuntimeError("cleanup private-test-secret")
        self.dropped = True

    def close(self):
        self.closed = True


@pytest.mark.parametrize("uri", [
    "http://user:password@127.0.0.1:19530", "http://127.0.0.1:19530?token=secret",
    "http://127.0.0.1:19530#secret", "file:///tmp/milvus.db", "http://example.org:19530",
    "http://127.0.0.1:invalid", "http://127.0.0.1:19530/path",
])
def test_invalid_uri_is_redacted(uri):
    with pytest.raises(MilvusConfigurationError, match="milvus_configuration_error"):
        configured(DOCUCHECK_MILVUS_URI=uri)


@pytest.mark.parametrize(("key", "value"), [
    ("DATABASE", 'default"; delete'), ("DIMENSION", "1"), ("DIMENSION", "32769"),
    ("TIMEOUT", "nan"), ("TIMEOUT", "inf"), ("TIMEOUT", "0"), ("TIMEOUT", "31"),
])
def test_configuration_bounds(key, value):
    with pytest.raises(MilvusConfigurationError):
        configured(**{f"DOCUCHECK_MILVUS_{key}": value})


def test_config_and_preview_do_not_expose_token():
    config = configured()
    result = preview(config)
    assert result["mode"] == "preview" and result["network_calls"] == 0
    assert result["dimension"] == 1024
    assert "private-test-secret" not in repr(config) + str(result)


def test_preview_cli_never_imports_sdk(monkeypatch, capsys):
    import builtins
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith("pymilvus")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert main([], environ={}) == 0
    assert '"network_calls": 0' in capsys.readouterr().out


def test_send_requires_credentials_before_constructing_client():
    with pytest.raises(MilvusProbeError, match="milvus_credentials_missing"):
        run_probe(MilvusConfig.from_env({}), client_factory=lambda config: pytest.fail())


def test_probe_filters_other_case_and_snapshot():
    client = FakeClient()
    result = run_probe(configured(), client_factory=lambda config: client)
    assert result["matched_ids"] == [1, 2]
    assert result["remaining_ids"] == [1]
    assert client.closed and client.dropped
    assert result["collection"].startswith("docucheck_probe_")
    for operation, options in client.calls:
        if operation in {"search", "query"}:
            assert options["consistency_level"] == "Strong"
            assert options["timeout"] <= 30
    vectors = next(options["data"] for op, options in client.calls if op == "upsert")
    assert len(vectors) == 5 and all(len(row["vector"]) == 1024 for row in vectors)


def test_existing_collection_is_not_deleted():
    client = FakeClient(existing=True)
    with pytest.raises(MilvusProbeError, match="probe_name_collision"):
        run_probe(configured(), client_factory=lambda config: client)
    assert not client.created and not client.dropped and client.closed


def test_search_failure_is_redacted_and_cleans_collection():
    client = FakeClient(fail_search=True)
    with pytest.raises(MilvusProbeError, match="milvus_operation_failed") as failure:
        run_probe(configured(), client_factory=lambda config: client)
    assert "private-test-secret" not in str(failure.value)
    assert client.closed and client.dropped


def test_scope_leak_is_rejected():
    client = FakeClient(ignore_scope=True)
    with pytest.raises(MilvusProbeError, match="milvus_scope_probe_failed"):
        run_probe(configured(), client_factory=lambda config: client)
    assert client.closed and client.dropped


def test_cleanup_failure_cannot_report_success():
    client = FakeClient(fail_cleanup=True)
    with pytest.raises(MilvusProbeError, match="milvus_cleanup_failed"):
        run_probe(configured(), client_factory=lambda config: client)
    assert client.closed


def test_connection_failure_is_redacted():
    def failing(config):
        raise RuntimeError("private-test-secret")

    with pytest.raises(MilvusProbeError, match="milvus_connection_failed"):
        run_probe(configured(), client_factory=failing)


def test_send_cli_missing_credentials_is_redacted(capsys):
    assert main(["--send"], environ={}) == 2
    assert "milvus_credentials_missing" in capsys.readouterr().out


def test_uncertain_create_reports_name_without_dropping_unknown_collection():
    class UncertainCreate(FakeClient):
        def create_collection(self, **kwargs):
            self.collection_name = kwargs["collection_name"]
            raise TimeoutError("private-test-secret")

    client = UncertainCreate()
    with pytest.raises(MilvusProbeError, match="milvus_operation_failed") as failure:
        run_probe(configured(), client_factory=lambda config: client)
    assert failure.value.collection == client.collection_name
    assert not client.dropped and client.closed


def test_client_close_failure_cannot_report_success():
    class BrokenClose(FakeClient):
        def close(self):
            raise RuntimeError("private-test-secret")

    client = BrokenClose()
    with pytest.raises(MilvusProbeError, match="milvus_client_close_failed"):
        run_probe(configured(), client_factory=lambda config: client)
    assert client.dropped


def test_stale_delete_visibility_is_rejected():
    class StaleDelete(FakeClient):
        def delete(self, **kwargs):
            pass

    client = StaleDelete()
    with pytest.raises(MilvusProbeError, match="milvus_delete_visibility_failed"):
        run_probe(configured(), client_factory=lambda config: client)
    assert client.closed and client.dropped


@pytest.mark.parametrize("foreign_field", ["project_id", "case_id", "snapshot_id"])
def test_each_scope_dimension_filters_independently(foreign_field):
    class ForeignScope(FakeClient):
        def upsert(self, **kwargs):
            super().upsert(**kwargs)
            foreign = self.rows[2]
            foreign.update(project_id="probe_project", case_id="probe_case",
                           snapshot_id="probe_snapshot")
            foreign[foreign_field] = "foreign_scope"

    client = ForeignScope()
    result = run_probe(configured(), client_factory=lambda config: client)
    assert result["matched_ids"] == [1, 2]


def test_search_accepts_additional_metadata_but_checks_required_scope():
    class ExtraMetadata(FakeClient):
        def search(self, **kwargs):
            response = super().search(**kwargs)
            for hit in response[0]:
                hit["entity"]["id"] = hit["id"]
            return response

    client = ExtraMetadata()
    assert run_probe(configured(), client_factory=lambda config: client)["status"] == "completed"


def test_cli_suppresses_sdk_error_logs(monkeypatch, caplog, capsys):
    import logging

    import backend.app.milvus_probe as module

    def failing(config):
        logging.getLogger("sdk-test").warning("SDK exception includes private-test-secret")
        raise MilvusProbeError("milvus_operation_failed")

    monkeypatch.setattr(module, "run_probe", failing)
    with caplog.at_level(logging.WARNING):
        assert main(["--send"], environ={"DOCUCHECK_MILVUS_TOKEN": "private-test-secret"}) == 2
    assert "private-test-secret" not in caplog.text + capsys.readouterr().out
