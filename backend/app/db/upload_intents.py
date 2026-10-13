"""Durable upload ownership, file publication and bounded orphan recovery."""
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import OperationalError

from .document_repository import DocumentError
from .models import document_versions, upload_intents

GRACE_SECONDS = 1800
_KEY = re.compile(r"uploads/([a-f0-9]{2})/([a-f0-9]{32})\.(pdf|xlsx)")


def _now(value=None):
    return value if value is not None else datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class UploadIntent:
    document_id: str
    storage_key: str
    owner_token: str


def managed_paths(root: Path, document_id: str, key: str):
    matched = _KEY.fullmatch(key)
    if matched is None or matched[2] != document_id or matched[1] != document_id[:2]:
        raise DocumentError("invalid_storage_key")
    paths = (root / key, root / "uploads" / ".staging" / f"{document_id}.{matched[3]}")
    for path in paths:
        try:
            if (path.is_symlink() or path.parent.is_symlink() or (root / "uploads").is_symlink()
                    or not path.resolve().is_relative_to(root)):
                raise DocumentError("invalid_storage_key")
        except (OSError, RuntimeError) as exc:
            raise DocumentError("invalid_storage_key") from exc
    return paths


class UploadIntents:
    def __init__(self, engine, data_root: Path):
        self.engine, self.data_root = engine, data_root.resolve()

    def create(self, document_id: str, storage_key: str, *, now=None) -> UploadIntent:
        managed_paths(self.data_root, document_id, storage_key)
        current = _now(now)
        intent = UploadIntent(document_id, storage_key, uuid4().hex)
        with self.engine.begin() as connection:
            connection.execute(insert(upload_intents).values(id=document_id, storage_key=storage_key,
                owner_token=intent.owner_token, state="pending", created_at=current,
                next_check_at=current + timedelta(seconds=GRACE_SECONDS)))
        return intent

    @contextmanager
    def transaction(self, intent: UploadIntent):
        with self.engine.begin() as connection:
            self.require_owner(connection, intent)
            # SQLite ignores FOR UPDATE. This also acquires its write lock
            # before filesystem publication; MySQL uses the row lock above.
            if connection.dialect.name == "sqlite":
                changed = connection.execute(update(upload_intents).where(
                    upload_intents.c.id == intent.document_id, upload_intents.c.state == "pending",
                    upload_intents.c.owner_token == intent.owner_token).values(owner_token=intent.owner_token))
                if changed.rowcount != 1:
                    raise DocumentError("upload_expired")
            yield connection

    @staticmethod
    def require_owner(connection, intent):
        row = connection.execute(select(upload_intents).where(
            upload_intents.c.id == intent.document_id).with_for_update()).mappings().first()
        if (row is None or row["state"] != "pending" or row["owner_token"] != intent.owner_token
                or row["storage_key"] != intent.storage_key):
            raise DocumentError("upload_expired")

    @staticmethod
    def complete(connection, intent):
        changed = connection.execute(delete(upload_intents).where(
            upload_intents.c.id == intent.document_id, upload_intents.c.state == "pending",
            upload_intents.c.owner_token == intent.owner_token))
        if changed.rowcount != 1:
            raise DocumentError("upload_expired")

    def reconcile(self, *, now=None, limit=20):
        if not 1 <= limit <= 100:
            raise ValueError("invalid_upload_recovery_limit")
        current = _now(now)
        with self.engine.begin() as connection:
            candidates = list(connection.scalars(select(upload_intents.c.id).where(
                upload_intents.c.next_check_at <= current).order_by(
                upload_intents.c.next_check_at, upload_intents.c.id).limit(limit).with_for_update(skip_locked=True)))
        result = {"checked": 0, "removed": 0, "retained": 0, "error_code": None}
        for document_id in candidates:
            try:
                with self.engine.begin() as connection:
                    row = connection.execute(select(upload_intents).where(
                        upload_intents.c.id == document_id, upload_intents.c.next_check_at <= current
                    ).with_for_update(skip_locked=True)).mappings().first()
                    if row is None:
                        continue
                    if connection.dialect.name == "sqlite":
                        changed = connection.execute(update(upload_intents).where(
                            upload_intents.c.id == document_id, upload_intents.c.next_check_at <= current
                        ).values(state="abandoned"))
                        if changed.rowcount != 1:
                            continue
                    paths = managed_paths(self.data_root, document_id, row["storage_key"])
                    # Current locking read across ALL snapshots/Cases, never
                    # a stale MySQL repeatable-read view of current documents.
                    referenced = connection.scalar(select(document_versions.c.id).where(
                        document_versions.c.storage_key == row["storage_key"]
                    ).limit(1).with_for_update()) is not None
                    removed = 0
                    for path in paths[1:] if referenced else paths:
                        existed = path.is_file()
                        path.unlink(missing_ok=True)
                        removed += int(existed)
                    # Keep an abandoned ownership tombstone and rescan it.
                    # A disconnected/paused writer may create a late file then
                    # exit; it cannot commit SQL but still needs future cleanup.
                    connection.execute(update(upload_intents).where(upload_intents.c.id == document_id).values(
                        state="abandoned", next_check_at=current + timedelta(seconds=GRACE_SECONDS)))
                    result["checked"] += 1
                    result["removed"] += removed
                    result["retained"] += int(referenced)
            except (DocumentError, OSError):
                result["error_code"] = "upload_recovery_failed"
            except OperationalError as exc:
                if self.engine.dialect.name == "sqlite" and getattr(exc.orig, "sqlite_errorcode", None) in {5, 6}:
                    continue  # Active upload owns SQLite's database write lock.
                raise
        return result
