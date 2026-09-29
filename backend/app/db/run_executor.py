"""Execute one claimed database Run against its parsed snapshot."""

from dataclasses import dataclass

from sqlalchemy import Engine, select

from ..agent.graph import AgentSession
from ..runtime.lifecycle import RunStatus
from .agent_snapshot import SnapshotLoadError, load_agent_snapshot
from .models import agent_runs, messages
from .run_heartbeat import run_heartbeat
from .run_repository import RepositoryError, RunRepository


@dataclass(frozen=True)
class ExecutionReport:
    run_id: str
    attempt_id: str
    status: str
    answer_text: str
    error_code: str | None
    model_calls: int


def execute_run(engine: Engine, run_id: str, attempt_id: str,
                provider, *, heartbeat_interval_seconds: float = 10.0) -> ExecutionReport:
    runs = RunRepository(engine)
    claimed = runs.claim(run_id, attempt_id)
    try:
        with run_heartbeat(runs, claimed,
                           interval_seconds=heartbeat_interval_seconds):
            with engine.connect() as connection:
                request = connection.execute(select(
                    agent_runs.c.case_id, agent_runs.c.conversation_id,
                    agent_runs.c.mode, messages.c.content,
                ).join(messages, agent_runs.c.message_id == messages.c.id).where(
                    agent_runs.c.id == run_id,
                )).mappings().one()
            try:
                repo = load_agent_snapshot(engine, request["case_id"],
                                           claimed.snapshot_id)
            except SnapshotLoadError as exc:
                answer = "当前资料快照尚不能供 Agent 完整读取，请完成解析或缩小资料后重试。"
                runs.finish(run_id, attempt_id, claimed.fence, claimed.snapshot_id,
                            RunStatus.PARTIAL, answer_text=answer, error_code=exc.code)
                return ExecutionReport(run_id, attempt_id, "partial", answer,
                                       exc.code, 0)
            session = AgentSession(repo, provider, mode=request["mode"],
                                   conversation_id=request["conversation_id"])
            result = session.ask(request["content"], run_id=run_id,
                                 attempt_id=attempt_id)
            if result.status == RunStatus.CANCELLED.value:
                runs.cancel(run_id)
            else:
                runs.finish(run_id, attempt_id, claimed.fence, claimed.snapshot_id,
                            RunStatus(result.status), answer_text=result.answer,
                            error_code=result.error,
                            model_id=getattr(provider, "model_id", None),
                            usage_json=result.usage,
                            outcome_unknown=any(event.get("outcome_unknown") is True
                                                for event in result.events))
            return ExecutionReport(run_id, attempt_id, result.status, result.answer,
                                   result.error, result.usage.get("model_calls", 0))
    except BaseException:
        try:
            runs.interrupt(run_id, attempt_id, claimed.fence)
        except RepositoryError:
            # A concurrent cancel, retry or revision change has already won.
            pass
        raise
