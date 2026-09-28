"""A model-directed LangGraph loop over bounded, source-backed tools."""
import json
import re
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import ValidationError

from ..field_contract import FieldKey
from ..providers import ProviderError
from .answer import render_answer, resolve_question_scope
from .capacity import review_capacity
from .context import (
    assemble,
    compact_observation,
    compact_tools,
    verified_result_handles,
)
from .policy import MODE_LIMITS, Budget, RunStopped
from .state import AgentState, RunContext, RunResult
from .tools import AnswerDraft, Clarification, ToolExecutor, tool_schemas

SAFE_VALIDATION_DETAILS = frozenset({
    "fact_field", "fact_value", "fact_qualifier", "fact_comparator",
    "candidate_scope", "candidate_pair", "candidate_field", "candidate_value",
    "extraction_schema", "extraction_block_too_large",
})
SAFE_COVERAGE_TYPES = frozenset({
    "batch_too_large", "batch_schema", "unexpected_field",
    "candidate_invalid_scope", "candidate_invalid_uncovered",
    "unverified_source", "unverified_value", "memory_coverage_incomplete",
})


class AgentSession:
    """One CLI conversation pinned to an immutable repository snapshot."""
    def __init__(self, repo, provider, *, max_decisions: int | None = None,
                 mode: str = "standard"):
        if mode not in MODE_LIMITS:
            raise ValueError("invalid_mode")
        if max_decisions is not None and max_decisions < 1:
            raise ValueError("invalid_decision_limit")
        self.repo, self.provider = repo, provider
        self.mode = mode
        self.max_decisions = max_decisions or MODE_LIMITS[mode]["max_decisions"]
        self.tools = ToolExecutor(repo)
        self.history = []
        self.focus: dict[str, str] | None = None
        self.pending_clarification: str | None = None
        self.conversation_id = uuid4().hex
        self._cancelled = False
        self.schemas = tool_schemas()

    def cancel(self) -> None:
        self._cancelled = True

    def _implicit_supplier(self, question: str) -> str | None:
        if not self.focus or re.search(r"全部供应商|所有供应商|各家|全面审查|完整审查|对比|比较", question):
            return None
        for source in self.repo.sources():
            supplier = source["supplier"]
            if not supplier:
                continue
            suffix = supplier.rsplit("-", 1)[-1]
            if (supplier.casefold() in question.casefold()
                    or (len(suffix) == 1 and suffix.isalpha()
                        and re.search(rf"供应商\s*{re.escape(suffix)}", question, re.IGNORECASE))):
                return None
        return self.focus["supplier"]

    def ask(self, question: str) -> RunResult:
        if not question.strip() or len(question) > 2000:
            raise ValueError("invalid_question")
        implicit_supplier = self._implicit_supplier(question)
        question_scope = resolve_question_scope(question, self.tools, implicit_supplier)
        allowed_suppliers = question_scope.suppliers
        compact_question = re.sub(r"\s+", "", question)
        matrix_suppliers = (tuple(sorted({source["supplier"] for source in self.repo.sources()
                                          if source["supplier"]}))
                            if ("完整审查" in compact_question
                                and ("全部供应商" in compact_question
                                     or "所有供应商" in compact_question)
                                and ("八项" in compact_question
                                     or "所有字段" in compact_question
                                     or "全部字段" in compact_question)) else ())
        if len(matrix_suppliers) < 2:
            matrix_suppliers = ()
        matrix_started = False
        matrix_fact_sets: dict[str, list[dict]] = {}
        matrix_attempts: dict[str, int] = {}
        matrix_evaluated: dict[str, list[str]] = {}
        context = RunContext(self.repo.case_id, self.repo.snapshot_id, self.repo.revision,
                             self.conversation_id, uuid4().hex, uuid4().hex,
                             getattr(self.provider, "model_id", None), self.mode)
        budget = Budget(**(MODE_LIMITS[self.mode] | {"max_decisions": self.max_decisions}),
                        context_window=getattr(self.provider, "context_window", 32768),
                        cancelled=lambda: self._cancelled)
        capacity = review_capacity(self.repo, question_scope,
                                   max_calls=budget.max_calls,
                                   max_decisions=budget.max_decisions)
        if capacity is not None:
            batch_count, minimum_calls = capacity
            answer = (f"本次完整审查预计至少需要 {batch_count} 个全量抽取批次、"
                      f"{minimum_calls} 次模型调用，超过当前模式预算或存在超大证据组。"
                      "请缩小资料或拆分审查；没有生成正式结论。")
            return RunResult(context, "partial", answer, "review_capacity",
                             budget.summary(), ())
        events = []

        def model_call(operation, messages, schema):
            schemas = schema if operation == "decide" else [schema]
            output_cap = (1536 if operation == "extract"
                          and schema["function"]["name"] == "FieldCandidates" else 512)
            options, reserved_input = budget.reserve(messages, schemas, output_cap=output_cap)
            event = {"kind": "model", "operation": operation, "sequence": len(events) + 1,
                     "status": "started"}
            events.append(event)
            try:
                response = (self.provider.decide(messages, schema, **options) if operation == "decide"
                            else self.provider.extract(messages, schema, **options))
                if not isinstance(response, AIMessage):
                    raise ProviderError("invalid_output")
                event.update(status="completed", usage=response.usage_metadata,
                             request_id=response.response_metadata.get("request_id"))
                if operation == "decide":
                    event["tool_call_count"] = len(response.tool_calls)
                budget.record(response, reserved_input, options["max_output_tokens"])
                return response
            except ProviderError as exc:
                budget.unknown_usage_calls += 1
                event.update(status="failed", error=exc.code, outcome_unknown=exc.outcome_unknown)
                if exc.detail:
                    event["failure_detail"] = exc.detail
                raise

        def stop(exc):
            code = exc.code if isinstance(exc, (RunStopped, ProviderError)) else "tool_error"
            status = "cancelled" if code == "cancelled" else "partial"
            if isinstance(exc, ProviderError):
                status = "failed"
            return {"status": status, "error": code, "next_node": "end",
                    "answer": f"本次处理未完成：{code}。没有生成新的正式结论。"}

        def matrix_next_call() -> tuple[str, dict] | None:
            if not matrix_suppliers or not matrix_started:
                return None
            for supplier in matrix_suppliers:
                if supplier in matrix_evaluated:
                    continue
                fact_sets = matrix_fact_sets.get(supplier)
                if (fact_sets is None or
                        (not all(item["complete"] for item in fact_sets)
                         and matrix_attempts[supplier] < 2)):
                    return "extract_facts", {"supplier": supplier,
                                             "field_keys": [field.value for field in FieldKey]}
                return "evaluate_checks", {"supplier": supplier,
                                           "fact_set_ids": [item["fact_set_id"]
                                                            for item in fact_sets]}
            return "submit_answer", {"kind": "check",
                                     "check_result_ids": [result_id
                                                          for supplier in matrix_suppliers
                                                          for result_id in matrix_evaluated[supplier]],
                                     "evidence_ids": []}

        def decide(state: AgentState):
            try:
                budget.decision()
                messages = compact_tools(state["messages"])
                handles = verified_result_handles(self.tools.results)
                guidance = [SystemMessage(content=(
                    "上一轮没有调用工具。继续当前用户问题时必须只调用一个可用工具，"
                    "不得直接回答；如范围不清，调用 request_clarification。"
                ))] if state["tool_repairs"] else []
                decision_messages = [messages[0], *guidance,
                                     *([handles] if handles else []), *messages[1:]]
                reply = model_call("decide", decision_messages, self.schemas)
                planned = matrix_next_call()
                if not reply.tool_calls:
                    if planned is None:
                        if state["tool_repairs"] >= 1:
                            return {"status": "failed", "error": "one_tool_required",
                                    "next_node": "end"}
                        return {"tool_repairs": 1, "next_node": "decide"}
                    chosen = {"id": uuid4().hex, "name": planned[0], "args": planned[1]}
                    events[-1]["policy_override"] = planned[0]
                else:
                    chosen = reply.tool_calls[0]
                if len(reply.tool_calls) > 1:
                    events[-1]["serialized_tool_calls"] = len(reply.tool_calls) - 1
                if planned is not None and (chosen["name"], chosen["args"]) != planned:
                    events[-1]["policy_override"] = planned[0]
                    chosen = {"id": chosen["id"], "name": planned[0], "args": planned[1]}
                if len(reply.tool_calls) != 1 or chosen != reply.tool_calls[0]:
                    reply = AIMessage(
                        content=reply.content, tool_calls=[chosen],
                        id=reply.id, response_metadata=reply.response_metadata,
                        usage_metadata=reply.usage_metadata,
                    )
                return {"messages": [*messages, reply], "next_node": "act"}
            except (RunStopped, ProviderError) as exc:
                return stop(exc)

        def act(state: AgentState):
            nonlocal matrix_started
            reply = state["messages"][-1]
            if len(reply.tool_calls) != 1:
                return {"status": "failed", "error": "one_tool_required", "next_node": "end"}
            deferred_calls = events[-1].get("serialized_tool_calls", 0)
            call = reply.tool_calls[0]
            name, args = call["name"], call["args"]
            event = {"kind": "tool", "tool": name, "sequence": len(events) + 1, "status": "started"}
            events.append(event)
            try:
                budget.tool()
                if name == "submit_answer":
                    try:
                        draft = AnswerDraft.model_validate(args)
                        answer, status = render_answer(
                            draft, self.tools, question,
                            required_supplier=implicit_supplier)
                    except (ValueError, ValidationError):
                        if state["repairs"] >= 1:
                            event.update(status="failed", error="answer_invalid")
                            return {"status": "partial", "error": "answer_invalid", "next_node": "end"}
                        event.update(status="rejected", error="answer_invalid")
                        return {"repairs": state["repairs"] + 1, "next_node": "decide",
                                "messages": [*state["messages"], ToolMessage(tool_call_id=call["id"],
                                    content=json.dumps({"status": "error", "error": "answer_invalid",
                                                        "instruction": "Use one verified result; extra evidence IDs must belong to it."}))]}
                    event.update(status="completed")
                    if status == "completed" and len(draft.check_result_ids) == 1:
                        result = self.tools.results[draft.check_result_ids[0]]
                        self.focus = {"supplier": result.supplier,
                                      "field_key": result.field_key}
                    elif status == "completed":
                        self.focus = None
                    if status == "completed":
                        self.pending_clarification = None
                    return {"answer": answer, "status": status, "next_node": "end"}
                if name == "request_clarification":
                    clarification = Clarification.model_validate(args)
                    event.update(status="completed")
                    self.pending_clarification = clarification.question
                    return {"answer": clarification.question, "status": "waiting_user", "next_node": "end"}
                if allowed_suppliers:
                    if (name == "search_evidence" and args.get("supplier") is None
                            and len(allowed_suppliers) == 1):
                        args = {**args, "supplier": next(iter(allowed_suppliers))}
                    if name in {"search_evidence", "extract_facts", "evaluate_checks",
                                "get_check_results"} and args.get("supplier") not in allowed_suppliers:
                        raise ValueError("supplier_scope")
                    if name == "read_source" and any(
                        block.supplier is not None and block.supplier not in allowed_suppliers
                        for block in self.repo.read(args.get("evidence_ids", []))
                    ):
                        raise ValueError("supplier_scope")
                result = self.tools.execute(name, args, model_call)
                if allowed_suppliers and any(
                    block.supplier is not None and block.supplier not in allowed_suppliers
                    for block in self.repo.read(result["evidence_refs"])
                ):
                    raise ValueError("supplier_scope")
                if name == "list_case_sources" and allowed_suppliers:
                    result["data"] = [source for source in result["data"]
                                      if source["supplier"] is None
                                      or source["supplier"] in allowed_suppliers]
                budget.check()
                if matrix_suppliers:
                    if name in {"list_case_sources", "search_evidence", "read_source"}:
                        matrix_started = True
                    elif name == "extract_facts" and "fact_sets" in result["data"]:
                        supplier = result["data"]["supplier"]
                        matrix_started = True
                        matrix_attempts[supplier] = matrix_attempts.get(supplier, 0) + 1
                        matrix_fact_sets[supplier] = result["data"]["fact_sets"]
                    elif name == "evaluate_checks" and "check_results" in result["data"]:
                        supplier = result["data"]["supplier"]
                        if len(result["data"]["check_results"]) == len(FieldKey):
                            matrix_evaluated[supplier] = result["data"]["check_result_ids"]
                event.update(status="completed", evidence_refs=result["evidence_refs"])
                if name == "extract_facts" and "fact_sets" in result["data"]:
                    coverage = []
                    for item in result["data"]["fact_sets"]:
                        issue_types = set()
                        for issue in item["coverage_issues"]:
                            kind = issue.partition(":")[0]
                            issue_types.add(kind if kind in SAFE_COVERAGE_TYPES else "other")
                        coverage.append({"field_key": item["field_key"],
                                         "complete": item["complete"],
                                         "issue_types": sorted(issue_types)})
                    event["field_coverage"] = coverage
                observation = compact_observation(name, result)
                if deferred_calls:
                    observation["deferred_tool_calls"] = deferred_calls
                    observation["instruction"] = "Other proposed calls were not run; request any still needed one at a time."
                return {"messages": [*state["messages"], ToolMessage(tool_call_id=call["id"],
                            content=json.dumps(observation, ensure_ascii=False))], "next_node": "decide"}
            except (RunStopped, ProviderError) as exc:
                event.update(status="failed", error=exc.code)
                if isinstance(exc, ProviderError) and exc.detail:
                    event["failure_detail"] = exc.detail
                return stop(exc)
            except (ValueError, ValidationError) as exc:
                code = str(exc) if str(exc) in {"evidence_scope", "supplier_scope", "fact_set_scope", "unknown_tool"} else "invalid_tool_result"
                detail = "schema_validation" if isinstance(exc, ValidationError) else "other_validation"
                if (len(exc.args) == 1 and isinstance(exc.args[0], str)
                        and exc.args[0] in SAFE_VALIDATION_DETAILS):
                    detail = exc.args[0]
                event.update(status="failed", error=code, failure_detail=detail)
                return {"status": "failed", "error": code, "next_node": "end"}

        builder = StateGraph(AgentState)
        builder.add_node("decide", decide)
        builder.add_node("act", act)
        builder.add_edge(START, "decide")
        for node in ("decide", "act"):
            builder.add_conditional_edges(node, lambda state: state["next_node"],
                                          {"decide": "decide", "act": "act", "end": END})
        with tracing_context(enabled=False):
            state = builder.compile().invoke({"messages": assemble(self.repo, self.history, question,
                self.schemas, budget.context_window, self.focus,
                self.pending_clarification), "status": "running", "answer": "", "error": None,
                "repairs": 0, "tool_repairs": 0, "next_node": "decide"},
                {"recursion_limit": 2 * self.max_decisions + 4,
                                                        "callbacks": []})
        answer = state["answer"] or f"本次处理未完成：{state['error']}。"
        if state["status"] in {"completed", "partial", "waiting_user"}:
            self.history.append((HumanMessage(content=question), AIMessage(content=answer)))
        return RunResult(context, state["status"], answer, state["error"], budget.summary(), tuple(events))
