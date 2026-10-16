"""Procurement setup chat must fit without discarding the buyer's fixed rules."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.dynamic import _schema
from backend.app.agent.policy import Budget, input_bound
from backend.app.criteria.changes import ChecklistPatch
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.repository import EvidenceRepository
from backend.tests.test_assistant_agent import Decisions


def test_history_handles_survive_provider_serialization_without_mutating_history():
    from langchain_openai.chat_models.base import _convert_message_to_dict

    history = [(HumanMessage(content="之前要求黑色", id="past-question"), AIMessage(content="已记录"))]
    provider = Decisions(("chat", {"answer": "你之前要求黑色。"}))
    assistant = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider,
                               history=history, history_reader=lambda *a, **kw: [])
    assert assistant.ask("之前什么颜色")["status"] == "completed"
    wire = [_convert_message_to_dict(message) for message in provider.messages[0]]
    assert any("past-question" in message.get("content", "") and "之前要求黑色" in message["content"] for message in wire)
    assert history[0][0].content == "之前要求黑色"
    # Removing the whole turn must also remove its handle.
    assistant.budget.max_prompt = input_bound(assistant._messages("继续"), assistant.schemas) - 1
    trimmed = assistant._messages("继续")
    assert "past-question" not in json.dumps([_convert_message_to_dict(message) for message in trimmed], ensure_ascii=False)
    assert assistant.context["history_turns_omitted"] == 1


@pytest.mark.parametrize("args", [
    {"message_ids": ["old", "old"], "part": "question"},
    {"message_ids": ["old", "older"], "part": "answer", "start_char": 1},
])
def test_history_tool_contracts_reject_invalid_pagination_before_reading(args):
    from pydantic import ValidationError

    from backend.app.agent.dynamic import HistoryArgs
    from backend.app.agent.tools import HistoryRead

    for model in (HistoryArgs, HistoryRead):
        assert not Draft202012Validator(model.model_json_schema()).is_valid(args)
        with pytest.raises(ValidationError):
            model.model_validate(args)


def test_history_tool_contracts_allow_distinct_messages_and_single_message_continuation():
    from backend.app.agent.dynamic import HistoryArgs
    from backend.app.agent.tools import HistoryRead

    for args in ({"message_ids": ["old", "older"], "part": "question"},
                 {"message_ids": ["old"], "part": "answer", "start_char": 30}):
        for model in (HistoryArgs, HistoryRead):
            Draft202012Validator(model.model_json_schema()).validate(args)
            assert model.model_validate(args).model_dump() == {"start_char": 0, **args}


def phone_requirements():
    # New synthetic fixture based on the reported seven generic, unspecified headings.
    entries = [
        ("机型/规格", "需明确具体型号、存储容量、颜色、版本（国行/其他）"),
        ("配置下限", "需明确内存、屏幕尺寸等硬性指标下限"),
        ("交付要求", "需明确交货期、交货地点、是否含运输/包装"),
        ("价格与付款", "需明确单价上限、币种、含税与否、付款方式与账期"),
        ("质量与售后", "需明确原装正品/全新未激活、保修年限、售后响应时效"),
        ("资质与证明", "需明确授权经销资质、发票类型（增值税专用发票等）"),
        ("验收方式", "需明确验收标准、抽检比例、不合格处理"),
    ]
    return ChecklistDefinition.model_validate({
        "items": [{"item_id": "phone", "name": "苹果手机"}],
        "criteria": [{"criterion_id": f"criterion-{i}", "item_id": "phone", "name": name,
                      "rule": {"type": "manual", "instruction": instruction},
                      "source": {"kind": "user_defined", "reason": "用户要求加入待明确项目"}}
                     for i, (name, instruction) in enumerate(entries)],
    })


def test_seven_manual_requirements_and_optional_memory_do_not_block_short_chat():
    base = phone_requirements()
    before = base.model_dump(mode="json")
    provider = Decisions(("chat", {"answer": "请先补充具体要求，并上传供应商报价资料。"}))
    layers = {"preferences": {"answer_length": "detailed"}, "summary": {"id": "s", "entries": [
        {"message_id": f"message-{i}", "question_excerpt": "过去的问题和回答" * 20} for i in range(20)]}}
    assistant = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider,
                               definition=base, memory_layers=layers)
    result = assistant.ask("所以我现在需要补充采购文件、供应商报价、承诺材料之类的")
    assert result["status"] == "completed" and result["usage"]["model_calls"] == 1
    assert input_bound(provider.messages[0], assistant.schemas) <= assistant.budget.max_prompt
    system = provider.messages[0][0].content
    for criterion in base.criteria:
        assert criterion.criterion_id in system and criterion.rule.instruction in system
    assert "不能改变" in system and provider.messages[0][-1].content.endswith("承诺材料之类的")
    assert base.model_dump(mode="json") == before


def test_optional_memory_falls_back_when_fixed_context_fits_but_summary_does_not():
    base = phone_requirements()
    provider = Decisions(("chat", {"answer": "可以先补齐资料。"}))
    assistant = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider, definition=base,
        memory_layers={"summary": {"id": "s", "entries": [{"message_id": "old", "question_excerpt": "旧" * 400}]}},
        history=[(HumanMessage(content="前一问题"), AIMessage(content="前一答复"))])
    # Fit exactly the mandatory context, forcing optional data out without enlarging any limit.
    baseline = AssistantAgent(assistant.repo, Decisions(), definition=base)
    baseline_messages = baseline._messages("继续")
    assistant.budget.max_prompt = input_bound(baseline_messages, baseline.schemas)
    result = assistant.ask("继续")
    assert result["status"] == "completed"
    audit = result["context"]
    assert audit["summary_entries_omitted"] == 1
    assert audit["input_bound"] <= assistant.budget.max_prompt
    assert audit["history_turns_selected"] + audit["history_turns_omitted"] == 1
    assert result["payload"]["candidate"] is None


def test_mandatory_context_overflow_is_explicit_and_never_calls_provider():
    provider = Decisions(("chat", {"answer": "不应调用"}))
    assistant = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider,
                               definition=phone_requirements(), budget=Budget(max_prompt=1000))
    result = assistant.ask("补充资料")
    assert result["status"] == "partial" and result["error"] == "context_budget"
    assert provider.messages == [] and result["usage"]["model_calls"] == 0
    assert "重试" in result["answer"] and "上下文" in result["answer"]
    assert result["context"]["mandatory_overflow"] is True
    assert result["payload"]["candidate"] is None and result["dynamic_payload"] is None


def test_large_history_is_removed_as_whole_turns_and_invalid_preferences_are_rejected():
    provider = Decisions(("chat", {"answer": "请先补充供应商资料。"}))
    history = [(HumanMessage(content=f"旧问题{i}" * 1000), AIMessage(content=f"旧回答{i}" * 1000)) for i in range(10)]
    assistant = AssistantAgent(EvidenceRepository.empty("case", "snap"), provider,
                               definition=phone_requirements(), history=history)
    result = assistant.ask("下一步")
    assert result["status"] == "completed"
    assert result["context"]["history_turns_omitted"] == 10
    messages = provider.messages[0]
    assert sum(m.type == "human" for m in messages) == sum(m.type == "ai" for m in messages) + 1
    assert messages[-1].content == "下一步"
    bad = AssistantAgent(assistant.repo, Decisions(), memory_layers={"preferences": {"actor": "admin"}}).ask("你好")
    assert bad["status"] == "partial" and bad["usage"]["model_calls"] == 0


@pytest.mark.parametrize("rule", [
    {"type": "numeric", "operator": "eq", "value": "20", "unit": "台"},
    {"type": "money", "operator": "lte", "value": "6000", "currency": "CNY", "per_unit": "台", "tax_basis": "included"},
    {"type": "enum", "operator": "eq", "values": ["全新"]},
    {"type": "presence", "description": "授权证明"},
    {"type": "duration", "operator": "lte", "value": "14", "calendar": "natural_days", "start_event": "订单确认"},
    {"type": "manual", "instruction": "确认验收方式"},
])
def test_compact_patch_schema_preserves_all_rule_types_and_rejects_invalid_patches(rule):
    assistant = AssistantAgent(EvidenceRepository.empty("case", "snap"), Decisions())
    compact = next(s["function"]["parameters"] for s in assistant.edit_schemas if s["function"]["name"] == "propose_changes")
    original = _schema("propose_changes", ChecklistPatch, "")['function']['parameters']
    # Metadata reduction must really create space, while all contracts remain enforced.
    assert len(json.dumps(compact)) < len(json.dumps(original))
    patch = {"items": [{"item_id": "phone", "name": "手机"}], "operations": [
        {"op": "add", "criterion": {"item_id": "phone", "name": "要求", "rule": rule}, "reason": "买方要求"}]}
    invalid = deepcopy(patch)
    invalid["operations"][0]["criterion"]["actor"] = "admin"
    missing = deepcopy(patch)
    del missing["operations"][0]["criterion"]["rule"]
    wrong_type = deepcopy(patch)
    wrong_type["operations"][0]["criterion"]["rule"]["type"] = "arbitrary_code"
    for schema in (compact, original):
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        assert validator.is_valid(patch)
        assert not any(validator.is_valid(bad) for bad in (invalid, missing, wrong_type))
