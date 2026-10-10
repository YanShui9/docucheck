"""Assistant routing within the existing fenced attempt, heartbeat and checkpoint."""

from sqlalchemy import select

from ..agent.assistant import AssistantAgent
from ..agent.dynamic import DynamicAgent
from ..criteria.models import ChecklistDefinition
from ..evidence.repository import digest
from ..runtime.lifecycle import RunStatus
from .agent_snapshot import SnapshotLoadError, load_agent_snapshot
from .assistant_repository import AssistantRepository
from .conversation_context import load_conversation_context
from .dynamic_repository import DynamicRepository, source_digest
from .memory_context import load_memory_context
from .models import agent_runs, document_versions
from .persisted_retrieval import persisted_retriever
from .run_heartbeat import run_heartbeat
from .run_repository import RepositoryError, RunRepository
from .source_repository import protect_sources


def execute_assistant(engine, claim, provider, *, heartbeat_interval_seconds=10.0,
                      retriever_factory=None):
    from .run_executor import ExecutionReport

    runs, store, dynamic = RunRepository(engine), AssistantRepository(engine), DynamicRepository(engine)
    retriever, result, memory = None, None, None
    try:
        with run_heartbeat(runs, claim, interval_seconds=heartbeat_interval_seconds):
            binding = store.binding(claim)
            with engine.connect() as connection:
                request = connection.execute(select(agent_runs).where(agent_runs.c.id == claim.run_id)).mappings().one()
            try:
                repo = load_agent_snapshot(engine, binding["case_id"], claim.snapshot_id, allow_empty=True)
            except SnapshotLoadError as exc:
                answer = "当前资料尚不能完整读取，请核对解析状态后重试。"
                runs.finish(claim.run_id, claim.active_attempt_id, claim.fence, claim.snapshot_id,
                            RunStatus.PARTIAL, answer_text=answer, error_code=exc.code)
                return ExecutionReport(claim.run_id, claim.active_attempt_id, "partial", answer, exc.code, 0)

            def validate():
                store.validate(claim)
                if not runs.has_source_access(claim.run_id, claim.active_attempt_id, claim.fence):
                    raise RepositoryError("source_access_changed")
                if hasattr(repo, "validate_access"):
                    try:
                        repo.validate_access()
                    except ValueError:
                        raise RepositoryError("source_access_changed") from None
                if repo.revision == 0:
                    with engine.connect() as connection:
                        if connection.scalar(select(document_versions.c.id).where(document_versions.c.case_id == repo.case_id,
                                document_versions.c.snapshot_id == repo.snapshot_id).limit(1)) is not None:
                            raise RepositoryError("source_access_changed")

            if repo.documents:
                retriever = (retriever_factory or persisted_retriever)(engine, repo, claim)
                repo = retriever.repository
            # Keep source revalidation under the same Case-first lock order as
            # submission/finish; standalone child -> parent locks can deadlock.
            edit_base = store.binding(claim, source=source_digest(repo))["effective_definition"]
            context = load_conversation_context(engine, claim.run_id, claim.active_attempt_id)
            memory = load_memory_context(engine, repo, claim, request, context)

            def validate_context():
                validate()
                try:
                    memory.validate()
                except ValueError:
                    raise RepositoryError("memory_context_changed") from None

            def formal(operation, budget):
                formal_binding = store.register_formal(claim, operation)
                formal_repo = repo
                if operation == "proposal":
                    buyer_ids = tuple(d.document_id for d in repo.documents if d.role == "requirement")
                    if not buyer_ids:
                        raise RepositoryError("buyer_sources_missing")
                    formal_repo = load_agent_snapshot(engine, repo.case_id, repo.snapshot_id, document_ids=buyer_ids)
                    formal_repo = protect_sources(engine, formal_repo, validate_context)
                agent = DynamicAgent(formal_repo, provider,
                    ChecklistDefinition.model_validate(formal_binding["definition"]) if formal_binding["definition"] else None,
                    mode=request["mode"], budget=budget, validate=validate_context,
                    cache_load=lambda batch, fingerprint: dynamic.load_batch(claim, batch, fingerprint),
                    cache_save=lambda batch, fingerprint, output: dynamic.save_batch(claim, batch, fingerprint, output),
                    history=context.history, memory_layers=memory.layers, history_reader=memory.history_reader,
                    item_bindings=formal_binding["item_bindings"])
                return agent.ask(context.question, operation="propose_checklist" if operation == "proposal" else "review")

            agent = AssistantAgent(repo, provider, mode=request["mode"], definition=edit_base,
                retriever=retriever, formal=formal, validate=validate_context,
                history=context.history, memory_layers=memory.layers, history_reader=memory.history_reader)
            result = agent.ask(context.question)
            if dynamic.retain_cancelled_usage(claim, result, provider):
                return ExecutionReport(claim.run_id, claim.active_attempt_id, "cancelled", "本次处理已取消。", "cancelled", result["usage"]["model_calls"])
            validate_context()
            payload = result["payload"]
            checkpoint = {"version": 5, "workflow": "assistant", "source_run_id": claim.run_id,
                "conversation_id": request["conversation_id"], "snapshot_id": claim.snapshot_id,
                "base_digest": binding["base_digest"], "checklist_revision": binding["checklist_revision"],
                "checklist_version_id": binding["checklist_version_id"], "payload_digest": digest(payload),
                "citation_offsets_version": payload["citation_offsets_version"],
                "focus": None, "pending_clarification": None}
            if binding["item_binding_digest"] is not None:
                checkpoint.update(item_binding_version_id=binding["item_binding_version_id"], item_binding_digest=binding["item_binding_digest"])
            if result["status"] == "cancelled":
                runs.cancel(claim.run_id)
                dynamic.retain_cancelled_usage(claim, result, provider)
            else:
                runs.finish(claim.run_id, claim.active_attempt_id, claim.fence, claim.snapshot_id,
                    RunStatus(result["status"]), answer_text=result["answer"], error_code=result["error"],
                    model_id=getattr(provider, "model_id", None), usage_json=result["usage"],
                    trace_events=result["events"], memory_json=checkpoint,
                    context_manifest=memory.manifest | {"assistant": checkpoint, "assistant_context": result["context"]},
                    dynamic_payload=result["dynamic_payload"], assistant_payload=payload,
                    outcome_unknown=any(e.get("outcome_unknown") is True for e in result["events"]))
            return ExecutionReport(claim.run_id, claim.active_attempt_id, result["status"], result["answer"], result["error"], result["usage"]["model_calls"])
    except BaseException as exc:
        if result is not None and dynamic.retain_cancelled_usage(claim, result, provider):
            return ExecutionReport(claim.run_id, claim.active_attempt_id, "cancelled", "本次处理已取消。", "cancelled", result["usage"]["model_calls"])
        try:
            runs.interrupt(claim.run_id, claim.active_attempt_id, claim.fence,
                usage_json=result["usage"] if result else None, model_id=getattr(provider, "model_id", None),
                error_code=getattr(exc, "code", None))
        except RepositoryError:
            pass
        raise
    finally:
        if retriever is not None:
            retriever.close()
