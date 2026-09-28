"""Host and container use the same URL builder with different endpoints."""

import pytest

from backend.app.db.connection import database_url


def test_database_url_uses_container_host_without_exposing_password(monkeypatch):
    monkeypatch.delenv("DOCUCHECK_DATABASE_URL", raising=False)
    monkeypatch.setenv("DOCUCHECK_MYSQL_PASSWORD", "local@secret")
    monkeypatch.setenv("DOCUCHECK_MYSQL_HOST", "mysql")
    monkeypatch.setenv("DOCUCHECK_MYSQL_PORT", "3306")
    url = database_url()
    assert url.host == "mysql" and url.port == 3306
    assert url.password == "local@secret"
    assert "local@secret" not in str(url)


@pytest.mark.parametrize("port", ["0", "65536", "invalid"])
def test_database_url_rejects_invalid_port(monkeypatch, port):
    monkeypatch.delenv("DOCUCHECK_DATABASE_URL", raising=False)
    monkeypatch.setenv("DOCUCHECK_MYSQL_PASSWORD", "local-secret")
    monkeypatch.setenv("DOCUCHECK_MYSQL_PORT", port)
    with pytest.raises(RuntimeError, match="database_port_invalid"):
        database_url()
