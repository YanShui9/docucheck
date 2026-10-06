"""Dynamic Agent integration with the existing fenced Run and memory lifecycle."""

from sqlalchemy import select

from ..criteria.models import ChecklistDefinition
from ..evidence.repository import digest
from ..runtime.lifecycle import RunStatus
from .agent_snapshot import SnapshotLoadError, load_agent_snapshot
from .conversation_context import load_conversation_context
from .dynamic_repository import DynamicRepository
from .memory_context import load_memory_context
from .models import agent_runs, document_versions
from .run_heartbeat import run_heartbeat
from .run_repository import RepositoryError, RunRepository
from .source_repository import protect_sources


def execute_dynamic(engine, claim, provider, *, heartbeat_interval_seconds=10.0):
    from ..agent.dynamic import DynamicAgent
    from .run_executor import ExecutionReport

    runs, store = RunRepository(engine), DynamicRepository(engine)
    result = None
    memory = None
    try:
        with run_heartbeat(runs, claim, interval_seconds=heartbeat_interval_seconds):
            binding = store.binding(claim)
            with engine.connect() as connection:
                request = (
                    connection.execute(
                        select(agent_runs).where(agent_runs.c.id == claim.run_id)
                    )
                    .mappings()
                    .one()
                )
                buyer_ids = (
                    tuple(
                        connection.scalars(
                            select(document_versions.c.id).where(
                                document_versions.c.case_id == binding["case_id"],
                                document_versions.c.snapshot_id == claim.snapshot_id,
                                document_versions.c.role == "requirement",
                            )
                        )
                    )
                    if binding["workflow"] == "proposal"
                    else None
                )
            try:
                if buyer_ids == ():
                    raise SnapshotLoadError("buyer_sources_missing")
                repo = load_agent_snapshot(
                    engine,
                    binding["case_id"],
                    claim.snapshot_id,
                    document_ids=buyer_ids,
                )
            except SnapshotLoadError as exc:
                answer = "本次所需资料还不能完整读取，请核对文件角色和解析状态后重试。"
                runs.finish(
                    claim.run_id,
                    claim.active_attempt_id,
                    claim.fence,
                    claim.snapshot_id,
                    RunStatus.PARTIAL,
                    answer_text=answer,
                    error_code=exc.code,
                )
                return ExecutionReport(
                    claim.run_id,
                    claim.active_attempt_id,
                    "partial",
                    answer,
                    exc.code,
                    0,
                )

            def validate():
                store.validate(claim)
                if not runs.has_source_access(
                    claim.run_id, claim.active_attempt_id, claim.fence
                ):
                    raise RepositoryError("source_access_changed")

            repo = protect_sources(engine, repo, validate)
            context = load_conversation_context(
                engine, claim.run_id, claim.active_attempt_id
            )
            memory = load_memory_context(engine, repo, claim, request, context)

            def validate_context():
                validate()
                memory.validate()

            previous_payload = None
            checkpoint_memory = context.memory or memory.task_memory
            if (
                isinstance(checkpoint_memory, dict)
                and checkpoint_memory.get("version") == 4
            ):
                previous_id = checkpoint_memory.get("source_run_id")
                if previous_id and binding["workflow"] == "review":
                    old, previous, _ = store.verified(previous_id)
                    if (
                        old["case_id"] == binding["case_id"]
                        and old["conversation_id"] == request["conversation_id"]
                        and old["snapshot_id"] == claim.snapshot_id
                        and old["checklist_version_id"]
                        == binding["checklist_version_id"]
                        and old["payload_digest"]
                        == checkpoint_memory.get("payload_digest")
                    ):
                        previous_payload = previous
                        if previous:
                            from .models import dynamic_batches

                            with engine.connect() as connection:
                                batches = list(
                                    connection.execute(
                                        select(dynamic_batches).where(
                                            dynamic_batches.c.run_id == previous_id
                                        )
                                    ).mappings()
                                )
                            for batch in batches:
                                store.save_batch(
                                    claim,
                                    batch["batch_id"],
                                    batch["input_digest"],
                                    batch["output_json"],
                                )

            agent = DynamicAgent(
                repo,
                provider,
                ChecklistDefinition.model_validate(binding["definition"])
                if binding["definition"]
                else None,
                mode=request["mode"],
                validate=validate_context,
                cache_load=lambda batch, fingerprint: store.load_batch(
                    claim, batch, fingerprint
                ),
                cache_save=lambda batch, fingerprint, output: store.save_batch(
                    claim, batch, fingerprint, output
                ),
                history=context.history,
                memory_layers=memory.layers,
                history_reader=memory.history_reader,
                previous_payload=previous_payload,
            )
            result = agent.ask(
                context.question,
                operation=(
                    "propose_checklist"
                    if binding["workflow"] == "proposal"
                    else "review"
                ),
            )
            if store.retain_cancelled_usage(claim, result, provider):
                return ExecutionReport(
                    claim.run_id,
                    claim.active_attempt_id,
                    "cancelled",
                    "本次处理已取消。",
                    "cancelled",
                    result.get("usage", {}).get("model_calls", 0),
                )
            validate_context()
            payload = result.get("payload")
            checkpoint = {
                "version": 4,
                "workflow": binding["workflow"],
                "source_run_id": claim.run_id,
                "snapshot_id": claim.snapshot_id,
                "checklist_version_id": binding["checklist_version_id"],
                "definition_digest": binding["definition_digest"],
                "payload_digest": digest(payload),
                "focus": None,
                "pending_clarification": None,
            }
            manifest = memory.manifest | {
                "dynamic": {
                    "checklist_version_id": binding["checklist_version_id"],
                    "checklist_revision": binding["checklist_revision"],
                    "payload_digest": digest(payload),
                }
            }
            if result["status"] == "cancelled":
                runs.cancel(claim.run_id)
            else:
                runs.finish(
                    claim.run_id,
                    claim.active_attempt_id,
                    claim.fence,
                    claim.snapshot_id,
                    RunStatus(result["status"]),
                    answer_text=result["answer"],
                    error_code=result.get("error"),
                    model_id=getattr(provider, "model_id", None),
                    usage_json=result.get("usage"),
                    trace_events=result.get("events"),
                    memory_json=checkpoint,
                    context_manifest=manifest,
                    dynamic_payload=payload,
                    outcome_unknown=any(
                        e.get("outcome_unknown") is True
                        for e in result.get("events", [])
                    ),
                )
            return ExecutionReport(
                claim.run_id,
                claim.active_attempt_id,
                result["status"],
                result["answer"],
                result.get("error"),
                result.get("usage", {}).get("model_calls", 0),
            )
    except BaseException as exc:
        if result is not None and store.retain_cancelled_usage(claim, result, provider):
            return ExecutionReport(
                claim.run_id,
                claim.active_attempt_id,
                "cancelled",
                "本次处理已取消。",
                "cancelled",
                result.get("usage", {}).get("model_calls", 0),
            )
        try:
            runs.interrupt(
                claim.run_id,
                claim.active_attempt_id,
                claim.fence,
                usage_json=result.get("usage") if result else None,
                model_id=getattr(provider, "model_id", None),
                error_code=exc.code if isinstance(exc, RepositoryError) else None,
            )
        except RepositoryError:
            if result is not None and store.retain_cancelled_usage(
                claim, result, provider
            ):
                return ExecutionReport(
                    claim.run_id,
                    claim.active_attempt_id,
                    "cancelled",
                    "本次处理已取消。",
                    "cancelled",
                    result.get("usage", {}).get("model_calls", 0),
                )
        raise
