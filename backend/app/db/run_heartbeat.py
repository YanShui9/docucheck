"""Keep a claimed Run's lease alive while synchronous Agent work executes."""

import logging
from contextlib import contextmanager
from threading import Event, Thread

from ..runtime.lifecycle import RunState
from .run_repository import RunRepository

LOGGER = logging.getLogger(__name__)


@contextmanager
def run_heartbeat(runs: RunRepository, claimed: RunState, *,
                  interval_seconds: float = 10.0):
    if not 0 < interval_seconds <= 60:
        raise ValueError("invalid_heartbeat_interval")
    stop = Event()

    def beat() -> None:
        while not stop.wait(interval_seconds):
            try:
                if not runs.heartbeat(claimed.run_id, claimed.active_attempt_id,
                                      claimed.fence):
                    return
            except Exception:  # noqa: BLE001 - a later beat may recover from DB outage
                LOGGER.warning("Run heartbeat failed; recovery may mark it unknown")

    thread = Thread(target=beat, name="docucheck-run-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
