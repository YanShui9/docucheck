"""Model usage reconciles conservative per-call reservations."""

from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.policy import Budget, input_bound


def test_known_input_usage_releases_unused_reservation_for_later_tool_steps():
    messages = [HumanMessage(content="检查内存")]
    bound = input_bound(messages, [])
    budget = Budget(max_input=2 * bound - 1)

    first, reserved_input = budget.reserve(messages, [])
    used = bound // 2
    reply = AIMessage(content="", usage_metadata={
        "input_tokens": used, "output_tokens": 10, "total_tokens": used + 10,
    })
    budget.record(reply, reserved_input, first["max_output_tokens"])

    assert budget.input_reserved == used
    budget.reserve(messages, [])
