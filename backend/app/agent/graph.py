"""A model-directed LangGraph loop over bounded, source-backed tools."""
import json
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import ValidationError

from ..providers import ProviderError
from .answer import render_answer
from .context import assemble, compact_tools
from .policy import Budget, RunStopped
from .state import AgentState, RunContext, RunResult
from .tools import AnswerDraft, Clarification, ToolExecutor, tool_schemas


class AgentSession:
    """One CLI conversation pinned to an immutable repository snapshot."""
    def __init__(self, repo, provider, *, max_decisions: int = 8):
        self.repo, self.provider = repo, provider
        self.max_decisions = max_decisions
        self.tools = ToolExecutor(repo)
        self.history = []
        self.conversation_id = uuid4().hex
        self._cancelled = False
        self.schemas = tool_schemas()

    def cancel(self) -> None:
        self._cancelled = True

    def ask(self, question: str) -> RunResult:
        if not question.strip() or len(question) > 2000:
            raise ValueError("invalid_question")
        context = RunContext(self.repo.case_id, self.repo.snapshot_id, self.repo.revision,
                             self.conversation_id, uuid4().hex, uuid4().hex,
                             getattr(self.provider, "model_id", None))
        budget = Budget(max_decisions=self.max_decisions,
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
                reply = model_call("decide", messages, self.schemas)
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
                        answer, status = render_answer(AnswerDraft.model_validate(args), self.tools, question)
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
                    return {"answer": answer, "status": status, "next_node": "end"}
                if name == "request_clarification":
                    clarification = Clarification.model_validate(args)
                    event.update(status="completed")
                    return {"answer": clarification.question, "status": "waiting_user", "next_node": "end"}
                result = self.tools.execute(name, args, model_call)
                budget.check()
                event.update(status="completed", evidence_refs=result["evidence_refs"])
                return {"messages": [*state["messages"], ToolMessage(tool_call_id=call["id"],
                            content=json.dumps(result, ensure_ascii=False))], "next_node": "decide"}
            except (RunStopped, ProviderError) as exc:
                event.update(status="failed", error=exc.code)
                return stop(exc)
            except (ValueError, ValidationError) as exc:
                code = str(exc) if str(exc) in {"evidence_scope", "supplier_scope", "fact_set_scope", "unknown_tool"} else "invalid_tool_result"
                event.update(status="failed", error=code)
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
                self.schemas, budget.context_window), "status": "running", "answer": "", "error": None,
                "repairs": 0, "next_node": "decide"}, {"recursion_limit": 2 * self.max_decisions + 4,
                                                        "callbacks": []})
        answer = state["answer"] or f"本次处理未完成：{state['error']}。"
        if state["status"] in {"completed", "partial", "waiting_user"}:
            self.history.append((HumanMessage(content=question), AIMessage(content=answer)))
        return RunResult(context, state["status"], answer, state["error"], budget.summary(), tuple(events))
