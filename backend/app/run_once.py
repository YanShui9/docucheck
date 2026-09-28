"""Preview or explicitly send one persisted Run through the configured Agent."""

import argparse
import json

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, select

from .db.agent_snapshot import SnapshotLoadError, load_agent_snapshot
from .db.connection import ROOT, database_url
from .db.models import agent_runs
from .db.run_executor import execute_run
from .db.run_repository import RepositoryError
from .providers import ProviderError, configured_provider


def main(argv: list[str] | None = None, *, engine: Engine | None = None,
         provider_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--send", action="store_true",
                        help="Call the configured model with this Run's question and evidence")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env", override=False)
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(database_url(), pool_pre_ping=True,
                               hide_parameters=True)
    try:
        with engine.connect() as connection:
            run = connection.execute(select(
                agent_runs.c.case_id, agent_runs.c.snapshot_id,
                agent_runs.c.status, agent_runs.c.active_attempt_id,
            ).where(agent_runs.c.id == args.run_id)).mappings().first()
        if run is None:
            print(json.dumps({"error": "run_not_found"}))
            return 2
        if run["active_attempt_id"] != args.attempt_id:
            print(json.dumps({"error": "stale_attempt"}))
            return 2
        if not args.send:
            try:
                repo = load_agent_snapshot(engine, run["case_id"],
                                           run["snapshot_id"])
                snapshot_status = "ready"
                document_count, evidence_count = len(repo.documents), len(repo.blocks)
            except SnapshotLoadError as exc:
                snapshot_status = exc.code
                document_count = evidence_count = 0
            print(json.dumps({"mode": "preview", "run_id": args.run_id,
                              "attempt_id": args.attempt_id,
                              "run_status": run["status"],
                              "snapshot_status": snapshot_status,
                              "document_count": document_count,
                              "evidence_count": evidence_count}, ensure_ascii=False))
            return 0
        if provider_factory is None:
            provider_factory = configured_provider
        try:
            provider = provider_factory()
            report = execute_run(engine, args.run_id, args.attempt_id,
                                 provider)
        except (RepositoryError, ProviderError) as exc:
            print(json.dumps({"error": exc.code}, ensure_ascii=False))
            return 2
        except KeyboardInterrupt:
            print(json.dumps({"error": "interrupted"}))
            return 130
        except Exception:  # noqa: BLE001 - CLI must not echo provider or document details.
            print(json.dumps({"error": "run_execution_failed"}))
            return 1
        print(json.dumps({"mode": "send", "run_id": report.run_id,
                          "attempt_id": report.attempt_id,
                          "status": report.status, "error_code": report.error_code,
                          "model_calls": report.model_calls,
                          "answer_text": report.answer_text}, ensure_ascii=False))
        return 0 if report.status in {"completed", "waiting_user"} else 1
    finally:
        if owns_engine:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
