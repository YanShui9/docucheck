"""Execute one claimed database Run against its parsed snapshot."""

from dataclasses import dataclass

from sqlalchemy import Engine, select

from ..agent.fact_memory import decode_fact_sets
from ..agent.graph import AgentSession
from ..agent.session_memory import _result, restore_session, serialize_session
from ..runtime.lifecycle import RunStatus
from .agent_snapshot import SnapshotLoadError, load_agent_snapshot
from .conversation_context import ConversationContextError, load_conversation_context
from .correction_recheck import CorrectionRecheck
from .correction_repository import CorrectionError
from .memory_context import load_memory_context
from .models import agent_runs
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
                provider, *, heartbeat_interval_seconds: float = 10.0, retriever_factory=None) -> ExecutionReport:
    runs = RunRepository(engine)
    claimed = runs.claim(run_id, attempt_id)
    from .assistant_repository import AssistantRepository
    if AssistantRepository(engine).exists(run_id):
        from .assistant_executor import execute_assistant
        return execute_assistant(engine, claimed, provider,
            heartbeat_interval_seconds=heartbeat_interval_seconds, retriever_factory=retriever_factory)
    from .dynamic_repository import DynamicRepository
    if DynamicRepository(engine).exists(run_id):
        from .dynamic_executor import execute_dynamic
        return execute_dynamic(engine, claimed, provider,
                               heartbeat_interval_seconds=heartbeat_interval_seconds)
    retriever = None
    result = None
    memory_context = None
    session = None
    try:
        with run_heartbeat(runs, claimed,
                           interval_seconds=heartbeat_interval_seconds):
            with engine.connect() as connection:
                request = connection.execute(select(
                    agent_runs.c.case_id, agent_runs.c.conversation_id,
                    agent_runs.c.mode, agent_runs.c.requester_key,
                ).where(agent_runs.c.id == run_id)).mappings().one()
            try:
                repo = load_agent_snapshot(engine, request["case_id"],
                                           claimed.snapshot_id)
            except SnapshotLoadError as exc:
                answer = "当前资料快照尚不能供 Agent 完整读取，请完成解析或缩小资料后重试。"
                runs.finish(run_id, attempt_id, claimed.fence, claimed.snapshot_id,
                            RunStatus.PARTIAL, answer_text=answer, error_code=exc.code)
                return ExecutionReport(run_id, attempt_id, "partial", answer,
                                       exc.code, 0)
            from .persisted_retrieval import persisted_retriever
            try:
                retriever = (retriever_factory or persisted_retriever)(engine, repo, claimed)
            except ValueError:
                raise RepositoryError("source_access_changed") from None
            repo = retriever.repository
            try:
                context = load_conversation_context(engine, run_id, attempt_id)
                memory_context = load_memory_context(engine, repo, claimed, request, context)
                session = AgentSession(repo, provider, mode=request["mode"], retriever=retriever,
                                       conversation_id=request["conversation_id"],
                                       memory_layers=memory_context.layers,
                                       context_validator=memory_context.validate,
                                       history_reader=memory_context.history_reader,
                                       cancelled=lambda: not runs.is_current(
                                           run_id, attempt_id, claimed.fence))
                memory = memory_context.task_memory or context.memory
                if memory is not None:
                    restore_session(session, memory, context.history)
                # SQL originals, with source IDs, define the actual recent turns.
                # A serialized cache may include partial or older turns.
                session.history = context.history
            except (ConversationContextError, ValueError):
                answer = "会话上下文未通过范围核验，请检查本轮记录。"
                runs.finish(run_id, attempt_id, claimed.fence, claimed.snapshot_id,
                            RunStatus.PARTIAL, answer_text=answer,
                            error_code="memory_scope")
                return ExecutionReport(run_id, attempt_id, "partial", answer,
                                       "memory_scope", 0)
            try:
                review = CorrectionRecheck(engine).load_for_run(
                    request["case_id"], claimed.snapshot_id, repo,
                )
                if review is not None:
                    fact_sets = decode_fact_sets(repo, [review["fact_set_json"]])
                    if len(fact_sets) != 1:
                        raise ValueError("correction_source_changed")
                    review_result = _result(session, review["check_result_json"])
                    session.tools.bind_review(next(iter(fact_sets.values())), review_result,
                                              review["review_status"])
            except (CorrectionError, ValueError) as exc:
                code = exc.code if isinstance(exc, CorrectionError) else "correction_source_changed"
                answer = "修正复核记录或来源未通过核验，没有生成新的正式结论。"
                runs.finish(run_id, attempt_id, claimed.fence, claimed.snapshot_id,
                            RunStatus.PARTIAL, answer_text=answer, error_code=code)
                return ExecutionReport(run_id, attempt_id, "partial", answer, code, 0)
            result = session.ask(context.question, run_id=run_id,
                                 attempt_id=attempt_id)
            if result.status == RunStatus.CANCELLED.value:
                try:
                    current = runs.get(run_id)
                except RepositoryError as exc:
                    if exc.code != "run_not_found":
                        raise
                else:
                    if current.status != RunStatus.CANCELLED:
                        if (current.status != RunStatus.RUNNING
                                or current.active_attempt_id != attempt_id
                                or current.fence != claimed.fence):
                            raise RepositoryError("stale_attempt")
                        runs.cancel(run_id)
            else:
                try:
                    memory_context.validate()
                except ValueError:
                    raise RepositoryError("memory_context_changed") from None
                runs.finish(run_id, attempt_id, claimed.fence, claimed.snapshot_id,
                            RunStatus(result.status), answer_text=result.answer,
                            error_code=result.error,
                            model_id=getattr(provider, "model_id", None),
                            usage_json=result.usage,
                            trace_events=result.events,
                            memory_json=serialize_session(session, result.answer),
                            context_manifest=memory_context.manifest | {"assembly": session.context_audit},
                            outcome_unknown=any(event.get("outcome_unknown") is True
                                                for event in result.events))
            return ExecutionReport(run_id, attempt_id, result.status, result.answer,
                                   result.error, result.usage.get("model_calls", 0))
    except BaseException as exc:
        try:
            manifest = (memory_context.manifest | {"assembly": session.context_audit}
                        if memory_context is not None and session is not None else None)
            runs.interrupt(run_id, attempt_id, claimed.fence,
                usage_json=result.usage if result is not None else None,
                model_id=getattr(provider, "model_id", None), context_manifest=manifest,
                error_code=exc.code if isinstance(exc, RepositoryError) else None)
        except RepositoryError:
            # A concurrent cancel, retry or revision change has already won.
            pass
        raise
    finally:
        if retriever is not None:
            retriever.close()
