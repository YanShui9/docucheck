"""Assistant routing within the existing fenced attempt, heartbeat and checkpoint."""

from sqlalchemy import select

from ..agent.assistant import AssistantAgent
from ..agent.assistant_answers import answer_digest
from ..agent.assistant_checklist_answers import checklist_answer_digest
from ..agent.dynamic import DynamicAgent
from ..agent.policy import RunStopped
from ..agent.semantic_memory import refresh_summary
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
from .semantic_memory_repository import SemanticMemoryRepository
from .source_repository import protect_sources
from .task_handoff_repository import TaskHandoffRepository


def execute_assistant(engine, claim, provider, *, heartbeat_interval_seconds=10.0,
                      retriever_factory=None):
    from .run_executor import ExecutionReport

    runs, store, dynamic = RunRepository(engine), AssistantRepository(engine), DynamicRepository(engine)
    retriever, result, memory = None, None, None
    manual_stamp = None
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
            authority = store.binding(claim, source=source_digest(repo))
            edit_base = authority["effective_definition"]
            context = load_conversation_context(engine, claim.run_id, claim.active_attempt_id)
            memory = load_memory_context(engine, repo, claim, request, context)
            semantic_store = SemanticMemoryRepository(engine)
            semantic_view = semantic_store.prepare(claim)
            semantic_summary = semantic_view["summary"]
            semantic_saved = None
            layers = dict(memory.layers)
            if semantic_summary is not None:
                layers["semantic_summary"] = semantic_summary
            handoff_store = TaskHandoffRepository(engine)
            handoff_view = handoff_store.prepare(claim)
            if handoff_view["context"] is not None:
                layers["task_handoff"] = handoff_view["context"]

            def semantic_manifest():
                return {"version": 1, "scope_digest": semantic_view["scope_digest"],
                        "input_digest": semantic_view["input_digest"],
                        "summary_id": semantic_summary["id"] if semantic_summary else None,
                        "summary_version": semantic_summary["version"] if semantic_summary else None,
                        "through_version": semantic_summary["through_version"] if semantic_summary else 0}

            def validate_context():
                validate()
                if manual_stamp is not None:
                    dynamic.validate_manual_previous(claim, manual_stamp)
                try:
                    memory.validate()
                    semantic_store.validate(claim, semantic_view, semantic_saved)
                    handoff_store.validate(claim, handoff_view)
                except ValueError:
                    raise RepositoryError("memory_context_changed") from None

            def formal(operation, budget):
                nonlocal manual_stamp
                formal_binding = store.register_formal(claim, operation)
                previous_payload = None
                if operation == "review":
                    previous_payload, manual_stamp = dynamic.manual_previous(claim)
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
                    previous_payload=previous_payload,
                    item_bindings=formal_binding["item_bindings"])
                return agent.ask(context.question, operation="propose_checklist" if operation == "proposal" else "review")

            def update_semantic(budget, events, audit, accept):
                nonlocal semantic_summary, semantic_saved

                def save(notes):
                    nonlocal semantic_summary, semantic_saved
                    semantic_summary = semantic_store.save(claim, semantic_view, notes)
                    semantic_saved = semantic_summary
                    return semantic_summary

                def check():
                    try:
                        validate_context()
                    except (ValueError, RepositoryError):
                        raise RunStopped("memory_context_changed") from None

                try:
                    return refresh_summary(provider, budget, events, semantic_view, audit=audit,
                        begin=lambda: semantic_store.begin_attempt(claim, semantic_view),
                        save=save, validate=check, accept=accept)
                except ValueError:
                    raise RunStopped("memory_context_changed") from None

            agent = AssistantAgent(repo, provider, mode=request["mode"], definition=edit_base,
                confirmed_definition=authority["confirmed_definition"], checklist_version_id=authority["checklist_version_id"],
                retriever=retriever, formal=formal, validate=validate_context,
                history=context.history, memory_layers=layers, history_reader=memory.history_reader,
                task_history_reader=(lambda ids, **kwargs: handoff_store.read_history(claim, handoff_view, ids, **kwargs))
                    if handoff_view["manifest"]["sources"] else None,
                semantic_refresh=update_semantic)
            result = agent.ask(context.question, formal_operation=store.retry_operation(claim))
            if dynamic.retain_cancelled_usage(claim, result, provider):
                return ExecutionReport(claim.run_id, claim.active_attempt_id, "cancelled", "本次处理已取消。", "cancelled", result["usage"]["model_calls"])
            validate_context()
            payload = result["payload"]
            checkpoint = {"version": 5, "workflow": "assistant", "source_run_id": claim.run_id,
                "conversation_id": request["conversation_id"], "snapshot_id": claim.snapshot_id,
                "base_digest": binding["base_digest"], "checklist_revision": binding["checklist_revision"],
                "checklist_version_id": binding["checklist_version_id"], "payload_digest": digest(payload),
                "citation_offsets_version": payload["citation_offsets_version"],
                "citation_context_version": payload["citation_context_version"],
                "focus": None, "pending_clarification": None}
            if payload.get("evidence_answer") is not None:
                checkpoint["evidence_answer_digest"] = answer_digest(payload)
            if payload.get("checklist_answer") is not None:
                checkpoint["checklist_answer_digest"] = checklist_answer_digest(payload)
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
                    context_manifest=memory.manifest | {"assistant": checkpoint, "assistant_context": result["context"],
                        "semantic_memory": semantic_manifest(), "task_handoff": handoff_view["manifest"]},
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
