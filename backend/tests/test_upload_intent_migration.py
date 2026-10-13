"""Upload ownership is installed and removed by the real migration."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from backend.app.db.models import document_versions, upload_intents


def test_upload_migration_creates_ownership_and_reference_index(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    metadata = MetaData()
    Table("document_versions", metadata, Column("id", String(32), primary_key=True),
        Column("storage_key", String(255), nullable=False))
    metadata.create_all(engine)
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0018_upload_intents.py"
    spec = importlib.util.spec_from_file_location("upload_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0017_item_bindings"
    try:
        with engine.begin() as connection:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            migration.upgrade()
            schema = inspect(connection)
            assert schema.has_table("upload_intents")
            assert {row["name"] for row in schema.get_indexes("document_versions")} == {"ix_documents_storage_key"}
            assert {row["name"] for row in schema.get_columns("upload_intents")} == set(upload_intents.c.keys())
            migration.downgrade()
            assert not inspect(connection).has_table("upload_intents")
            assert inspect(connection).get_indexes("document_versions") == []
    finally:
        engine.dispose()


def test_upload_schema_and_reference_index_compile_for_mysql():
    # Compilation proves dialect compatibility, not actual locking behavior.
    table = str(CreateTable(upload_intents).compile(dialect=mysql.dialect()))
    reference = next(index for index in document_versions.indexes if index.name == "ix_documents_storage_key")
    index = str(CreateIndex(reference).compile(dialect=mysql.dialect()))
    assert "ENGINE=InnoDB" in table and "ck_upload_intents_state" in table
    assert "document_versions (storage_key)" in index
