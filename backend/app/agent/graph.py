"""A model-directed LangGraph loop over bounded, source-backed tools."""
import json
import re
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import ValidationError

from ..providers import ProviderError
from .answer import render_answer
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
        context = RunContext(self.repo.case_id, self.repo.snapshot_id, self.repo.revision,
                             self.conversation_id, uuid4().hex, uuid4().hex,
                             getattr(self.provider, "model_id", None), self.mode)
        budget = Budget(**(MODE_LIMITS[self.mode] | {"max_decisions": self.max_decisions}),
                        context_window=getattr(self.provider, "context_window", 32768),
                        cancelled=lambda: self._cancelled)
        events = []

        def model_call(operation, messages, schema):
            schemas = schema if operation == "decide" else [schema]
            options, reserved_input = budget.reserve(messages, schemas)
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
                budget.record(response, reserved_input, options["max_output_tokens"])
                return response
            except ProviderError as exc:
                budget.unknown_usage_calls += 1
                event.update(status="failed", error=exc.code, outcome_unknown=exc.outcome_unknown)
                raise

        def stop(exc):
            code = exc.code if isinstance(exc, (RunStopped, ProviderError)) else "tool_error"
            status = "cancelled" if code == "cancelled" else "partial"
            if isinstance(exc, ProviderError):
                status = "failed"
            return {"status": status, "error": code, "next_node": "end",
                    "answer": f"本次处理未完成：{code}。没有生成新的正式结论。"}

        def decide(state: AgentState):
            try:
                budget.decision()
                messages = compact_tools(state["messages"])
                handles = verified_result_handles(self.tools.results)
                decision_messages = ([messages[0], handles, *messages[1:]]
                                     if handles else messages)
                reply = model_call("decide", decision_messages, self.schemas)
                return {"messages": [*messages, reply], "next_node": "act"}
            except (RunStopped, ProviderError) as exc:
                return stop(exc)

        def act(state: AgentState):
            reply = state["messages"][-1]
            if len(reply.tool_calls) != 1:
                return {"status": "failed", "error": "one_tool_required", "next_node": "end"}
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
                if implicit_supplier:
                    if name == "search_evidence":
                        args = {**args, "supplier": args.get("supplier") or implicit_supplier}
                    if name in {"search_evidence", "extract_facts", "evaluate_checks",
                                "get_check_results"} and args.get("supplier") != implicit_supplier:
                        raise ValueError("supplier_scope")
                    if name == "read_source" and any(
                        block.supplier not in (None, implicit_supplier)
                        for block in self.repo.read(args.get("evidence_ids", []))
                    ):
                        raise ValueError("supplier_scope")
                result = self.tools.execute(name, args, model_call)
                budget.check()
                event.update(status="completed", evidence_refs=result["evidence_refs"])
                observation = compact_observation(name, result)
                return {"messages": [*state["messages"], ToolMessage(tool_call_id=call["id"],
                            content=json.dumps(observation, ensure_ascii=False))], "next_node": "decide"}
            except (RunStopped, ProviderError) as exc:
                event.update(status="failed", error=exc.code)
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
                "repairs": 0, "next_node": "decide"}, {"recursion_limit": 2 * self.max_decisions + 4,
                                                        "callbacks": []})
        answer = state["answer"] or f"本次处理未完成：{state['error']}。"
        if state["status"] in {"completed", "partial", "waiting_user"}:
            self.history.append((HumanMessage(content=question), AIMessage(content=answer)))
        return RunResult(context, state["status"], answer, state["error"], budget.summary(), tuple(events))
