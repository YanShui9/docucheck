"""Project membership and Case access for the OIDC workbench."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, insert, select, update

from ..api.auth import Principal
from .models import agent_runs, cases, conversations, project_members, projects


class WorkspaceAccessError(ValueError):
    def __init__(self, code: str = "not_found"):
        self.code = code
        super().__init__(code)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _utc(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


class WorkspaceRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def create_project(self, actor: Principal, name: str) -> dict:
        name = name.strip()
        if not 1 <= len(name) <= 120:
            raise WorkspaceAccessError("invalid_project_name")
        project_id, now = uuid4().hex, _now()
        with self.engine.begin() as connection:
            connection.execute(insert(projects).values(
                id=project_id, name=name, created_at=now,
            ))
            connection.execute(insert(project_members).values(
                project_id=project_id, principal_key=actor.key,
                role="owner", created_at=now,
            ))
        return {"id": project_id, "name": name, "role": "owner",
                "created_at": _utc(now)}

    def list_projects(self, actor: Principal) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(
                projects.c.id, projects.c.name, projects.c.created_at,
                project_members.c.role,
            ).join(project_members, projects.c.id == project_members.c.project_id)
                .where(project_members.c.principal_key == actor.key)
                .order_by(projects.c.created_at, projects.c.id)).all()
        return [{"id": row.id, "name": row.name, "role": row.role,
                 "created_at": _utc(row.created_at)} for row in rows]

    def require_project(self, project_id: str, actor: Principal,
                        action: str) -> str:
        with self.engine.connect() as connection:
            role = connection.scalar(select(project_members.c.role).where(
                project_members.c.project_id == project_id,
                project_members.c.principal_key == actor.key,
            ))
        if role is None or (action == "owner" and role != "owner") or (
                action == "write" and role == "viewer"):
            raise WorkspaceAccessError()
        return role

    def add_member(self, project_id: str, actor: Principal,
                   member: Principal, role: str) -> dict:
        if role not in {"owner", "editor", "viewer"}:
            raise WorkspaceAccessError("invalid_role")
        with self.engine.begin() as connection:
            owners = connection.scalars(select(
                project_members.c.principal_key,
            ).where(project_members.c.project_id == project_id,
                    project_members.c.role == "owner")
                .order_by(project_members.c.principal_key)
                .with_for_update()).all()
            if actor.key not in owners:
                raise WorkspaceAccessError()
            if member.key in owners and role != "owner" and len(owners) == 1:
                raise WorkspaceAccessError("last_project_owner")
            existing = connection.scalar(select(project_members.c.role).where(
                project_members.c.project_id == project_id,
                project_members.c.principal_key == member.key,
            ))
            if existing is None:
                connection.execute(insert(project_members).values(
                    project_id=project_id, principal_key=member.key,
                    role=role, created_at=_now(),
                ))
            else:
                connection.execute(update(project_members).where(
                    project_members.c.project_id == project_id,
                    project_members.c.principal_key == member.key,
                ).values(role=role))
        return {"project_id": project_id, "principal_key": member.key,
                "role": role}

    def create_case(self, project_id: str, actor: Principal) -> dict:
        self.require_project(project_id, actor, "write")
        case_id, now = uuid4().hex, _now()
        with self.engine.begin() as connection:
            connection.execute(insert(cases).values(
                id=case_id, project_id=project_id, revision=0,
                created_at=now, updated_at=now,
            ))
        return {"id": case_id, "project_id": project_id,
                "revision": 0, "created_at": _utc(now)}

    def require_case(self, case_id: str, actor: Principal,
                     action: str) -> str:
        with self.engine.connect() as connection:
            row = connection.execute(select(
                cases.c.project_id, project_members.c.role,
            ).select_from(cases.outerjoin(
                project_members,
                (cases.c.project_id == project_members.c.project_id) &
                (project_members.c.principal_key == actor.key),
            )).where(cases.c.id == case_id)).first()
        if row is None or row.project_id is None or row.role is None or (
                action == "owner" and row.role != "owner") or (
                action == "write" and row.role == "viewer"):
            raise WorkspaceAccessError()
        return row.project_id

    def case_for_conversation(self, conversation_id: str) -> str:
        with self.engine.connect() as connection:
            case_id = connection.scalar(select(conversations.c.case_id).where(
                conversations.c.id == conversation_id,
            ))
        if case_id is None:
            raise WorkspaceAccessError()
        return case_id

    def case_for_run(self, run_id: str) -> str:
        with self.engine.connect() as connection:
            case_id = connection.scalar(select(agent_runs.c.case_id).where(
                agent_runs.c.id == run_id,
            ))
        if case_id is None:
            raise WorkspaceAccessError()
        return case_id

    def list_cases(self, project_id: str, actor: Principal) -> list[dict]:
        self.require_project(project_id, actor, "read")
        with self.engine.connect() as connection:
            rows = connection.execute(select(cases).where(
                cases.c.project_id == project_id,
                cases.c.deletion_requested_at.is_(None),
            ).order_by(cases.c.created_at.desc(), cases.c.id.desc())).mappings().all()
        return [{"id": row["id"], "project_id": project_id,
                 "revision": row["revision"],
                 "created_at": _utc(row["created_at"]),
                 "updated_at": _utc(row["updated_at"])} for row in rows]

    def get_case(self, case_id: str, actor: Principal) -> dict:
        project_id = self.require_case(case_id, actor, "read")
        with self.engine.connect() as connection:
            row = connection.execute(select(cases).where(
                cases.c.id == case_id,
            )).mappings().one()
        return {"id": case_id, "project_id": project_id,
                "revision": row["revision"],
                "created_at": _utc(row["created_at"]),
                "updated_at": _utc(row["updated_at"])}
