"""Continuously deliver committed Run Outbox events to the Celery broker."""

import argparse
import json
import time
from dataclasses import asdict

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

from .db.connection import ROOT, database_url
from .db.outbox_dispatcher import PublishRun, dispatch_once


def _publish(run_id: str, attempt_id: str, event_key: str) -> None:
    from .celery_app import app

    # task_id is traceable, not a broker-side de-duplication guarantee.
    app.send_task("docucheck.execute_run", args=(run_id, attempt_id),
                  task_id=event_key, queue="runs")


def main(argv: list[str] | None = None, *, engine: Engine | None = None,
         publish: PublishRun | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100 or not 0.1 <= args.poll_seconds <= 60:
        parser.error("invalid dispatch limits")
    load_dotenv(ROOT / ".env", override=False)
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(database_url(), pool_pre_ping=True,
                               hide_parameters=True)
    sender = publish or _publish
    try:
        while True:
            summary = dispatch_once(engine, sender, limit=args.limit)
            if args.once or summary.claimed:
                print(json.dumps(asdict(summary)), flush=True)
            if args.once:
                return 0 if summary.retried == 0 else 1
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 130
    finally:
        if owns_engine:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
