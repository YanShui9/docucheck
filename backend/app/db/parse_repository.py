"""Database leases for background parsing of the current Case snapshot."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Engine, and_, or_, select, update

from .models import cases, document_versions, input_snapshots


@dataclass(frozen=True)
class ParseClaim:
    case_id: str
    snapshot_id: str
    document_id: str
    claim_id: str


def _now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(tzinfo=None)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


class ParseRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def claim(self, *, limit: int = 1, lease_seconds: int = 240,
              now: datetime | None = None) -> list[ParseClaim]:
        if (type(limit) is not int or not 1 <= limit <= 20
                or type(lease_seconds) is not int or not 210 <= lease_seconds <= 3600):
            raise ValueError("invalid_parse_limits")
        current = _now(now)
        claims = []
        with self.engine.begin() as connection:
            rows = connection.execute(select(
                document_versions.c.id, document_versions.c.case_id,
                document_versions.c.snapshot_id,
                document_versions.c.parse_status,
                document_versions.c.parse_claim_id,
                document_versions.c.parse_attempts,
                document_versions.c.coverage_json,
            ).join(input_snapshots,
                   document_versions.c.snapshot_id == input_snapshots.c.id)
                .join(cases, input_snapshots.c.case_id == cases.c.id)
                .where(input_snapshots.c.revision == cases.c.revision,
                       or_(document_versions.c.parse_status == "pending",
                           and_(document_versions.c.parse_status == "processing",
                                document_versions.c.parse_lease_until <= current)))
                .order_by(document_versions.c.created_at, document_versions.c.id)
                .limit(limit).with_for_update(skip_locked=True)).mappings().all()
            for row in rows:
                if row["parse_attempts"] >= 3:
                    connection.execute(update(document_versions).where(
                        document_versions.c.id == row["id"],
                        document_versions.c.parse_status == row["parse_status"],
                        document_versions.c.parse_claim_id == row["parse_claim_id"],
                    ).values(parse_status="failed", parse_claim_id=None,
                             parse_lease_until=None, parser_version=None,
                             coverage_json={
                                 "preflight": (row["coverage_json"] or {}).get(
                                     "preflight", {}),
                                 "coverage_issues": ["worker_lost"],
                             }))
                    continue
                claim_id = uuid4().hex
                changed = connection.execute(update(document_versions).where(
                    document_versions.c.id == row["id"],
                    document_versions.c.parse_status == row["parse_status"],
                    document_versions.c.parse_claim_id == row["parse_claim_id"],
                ).values(parse_status="processing", parse_claim_id=claim_id,
                         parse_lease_until=current + timedelta(seconds=lease_seconds),
                         parse_attempts=document_versions.c.parse_attempts + 1))
                if changed.rowcount == 1:
                    claims.append(ParseClaim(row["case_id"], row["snapshot_id"],
                                             row["id"], claim_id))
        return claims

    def fail_claim(self, claim: ParseClaim, code: str,
                   *, now: datetime | None = None) -> bool:
        if code not in {"parser_not_configured", "worker_failed"}:
            raise ValueError("invalid_parse_error")
        current = _now(now)
        with self.engine.begin() as connection:
            row = connection.execute(select(document_versions.c.coverage_json).where(
                document_versions.c.id == claim.document_id,
                document_versions.c.case_id == claim.case_id,
                document_versions.c.snapshot_id == claim.snapshot_id,
                document_versions.c.parse_status == "processing",
                document_versions.c.parse_claim_id == claim.claim_id,
                document_versions.c.parse_lease_until > current,
            ).with_for_update()).first()
            if row is None:
                return False
            changed = connection.execute(update(document_versions).where(
                document_versions.c.id == claim.document_id,
                document_versions.c.parse_status == "processing",
                document_versions.c.parse_claim_id == claim.claim_id,
                document_versions.c.parse_lease_until > current,
            ).values(parse_status="failed", parse_claim_id=None,
                     parse_lease_until=None, parser_version=None,
                     coverage_json={"preflight": (row.coverage_json or {}).get(
                         "preflight", {}), "coverage_issues": [code]}))
            return changed.rowcount == 1
