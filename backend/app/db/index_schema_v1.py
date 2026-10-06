"""Frozen 0012 index schema shared by metadata and migration; extend via later migrations."""
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql


def define_index_tables(metadata: MetaData) -> tuple[Table, Table]:
    time = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    manifests = Table(
        "index_manifests", metadata,
        Column("id", String(64), primary_key=True),
        Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
        Column("snapshot_id", String(64), nullable=False),
        # Keep locator metadata after original-document cleanup. No document FK.
        Column("document_id", String(32), nullable=False),
        Column("project_key", String(256), nullable=False),
        Column("embedding_revision", String(256), nullable=False),
        Column("dimension", Integer, nullable=False),
        Column("chunk_schema", String(64), nullable=False),
        Column("collection_name", String(128), nullable=False),
        Column("generation", String(32), nullable=False, unique=True),
        Column("status", String(20), nullable=False),
        Column("fingerprint", String(64)),
        Column("expected_count", Integer, nullable=False, server_default="0"),
        Column("ingested_count", Integer, nullable=False, server_default="0"),
        Column("cleanup_claim_id", String(32)),
        Column("cleanup_lease_until", time),
        Column("next_cleanup_at", time),
        Column("cleanup_attempts", Integer, nullable=False, server_default="0"),
        Column("error_code", String(64)),
        Column("created_at", time, nullable=False),
        Column("updated_at", time, nullable=False),
        # Source snapshots are physically removed during Case cleanup. This locator survives.
        UniqueConstraint("case_id", "snapshot_id", "document_id", "id", name="uq_index_manifest_scope"),
        CheckConstraint("status IN ('indexing','ready','deleting','deleted')", name="ck_index_manifest_status"),
        CheckConstraint("dimension >= 2 AND dimension <= 32768", name="ck_index_manifest_dimension"),
        CheckConstraint("expected_count >= 0 AND ingested_count >= 0 AND ingested_count <= expected_count",
                        name="ck_index_manifest_counts"),
        mysql_engine="InnoDB",
    )
    Index("ix_index_manifest_cleanup", manifests.c.status, manifests.c.next_cleanup_at,
          manifests.c.cleanup_lease_until)
    Index("ix_index_manifest_scope", manifests.c.case_id, manifests.c.snapshot_id, manifests.c.document_id)
    jobs = Table(
        "document_index_jobs", metadata,
        Column("document_id", String(32), ForeignKey("document_versions.id", ondelete="CASCADE"), primary_key=True),
        Column("case_id", String(32), nullable=False),
        Column("snapshot_id", String(64), nullable=False),
        Column("status", String(20), nullable=False),
        Column("target_key", String(64)),
        Column("manifest_id", String(64)),
        Column("claim_id", String(32)),
        Column("lease_until", time),
        Column("fence", Integer, nullable=False, server_default="0"),
        Column("attempts", Integer, nullable=False, server_default="0"),
        Column("available_at", time, nullable=False),
        Column("error_code", String(64)),
        Column("created_at", time, nullable=False),
        Column("updated_at", time, nullable=False),
        ForeignKeyConstraint(["case_id", "snapshot_id", "document_id"],
                             ["document_versions.case_id", "document_versions.snapshot_id", "document_versions.id"],
                             name="fk_index_job_document_scope", ondelete="CASCADE"),
        ForeignKeyConstraint(["case_id", "snapshot_id", "document_id", "manifest_id"],
                             ["index_manifests.case_id", "index_manifests.snapshot_id",
                              "index_manifests.document_id", "index_manifests.id"], name="fk_index_job_manifest_scope"),
        CheckConstraint("status IN ('pending','indexing','ready','failed','deleting')", name="ck_index_job_status"),
        CheckConstraint("fence >= 0 AND attempts >= 0", name="ck_index_job_counters"),
        mysql_engine="InnoDB",
    )
    Index("ix_index_jobs_due", jobs.c.status, jobs.c.available_at, jobs.c.lease_until)
    return jobs, manifests
