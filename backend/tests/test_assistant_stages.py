"""Growing checklists use bounded tool/context stages, not a larger budget."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.policy import input_bound
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.repository import EvidenceRepository
from backend.tests.test_dynamic_agent import call


class StageProvider:
    context_window = 32768

    def __init__(self, *actions):
        self.actions = iter(actions)
        self.requests = []

    def decide(self, messages, schemas, **options):
        self.requests.append((messages, schemas))
        name, args = next(self.actions)
        return call(name, args)


def definition(count, *, verbose=False):
    return ChecklistDefinition.model_validate({"items": [{"item_id": "phone", "name": "苹果手机"}], "criteria": [
        {"criterion_id": f"criterion-{i}", "item_id": "phone", "name": f"审查要求{i}",
         "rule": {"type": "manual", "instruction": ("供应商应提供详细采购验收说明，具体阈值由买方确认。" * 30 if verbose else "具体指标需要买方明确后核对。")},
         "conditions": ["符合已确认采购口径"], "source": {"kind": "user_defined", "reason": "合成买方要求"}}
        for i in range(count)]})


def addition():
    return {"items": [], "operations": [{"op": "add", "reason": "买方要求补充交期方向", "criterion": {
        "item_id": "phone", "name": "交期", "rule": {"type": "manual", "instruction": "需明确交付起算事件、天数、自然日或工作日"}}}]}


@pytest.mark.parametrize("max_output,expected", [(6000, "completed"), (1000, "partial")])
def test_candidate_editor_has_json_capacity_inside_shared_budget(max_output, expected):
    from backend.app.agent.policy import Budget
    from backend.app.providers.base import ProviderError

    class OutputLimited(StageProvider):
        def decide(self, messages, schemas, **options):
            editing = any(s["function"]["name"] == "propose_changes" for s in schemas)
            if editing and options["max_output_tokens"] < 2000:
                raise ProviderError("invalid_output", detail="output_limit")
            if editing:
                assert options["max_output_tokens"] <= 4096
            else:
                assert options["max_output_tokens"] == min(1536, max_output)
            return super().decide(messages, schemas, **options)

    provider = OutputLimited(("prepare_changes", {"criterion_ids": [], "item_ids": ["phone"]}),
                             ("propose_changes", addition()))
    result = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider, definition=definition(10),
                            budget=Budget(max_output=max_output)).ask("增加交期")
    assert result["status"] == expected
    assert result["usage"]["output_reserved"] <= max_output
    if expected == "completed":
        assert len(result["payload"]["candidate"]["criteria"]) == 11
    else:
        assert result["payload"]["candidate"] is None


@pytest.mark.parametrize("count", [10, 40, 99])
def test_append_deadline_to_growing_preview_preserves_all_existing_requirements(count):
    base = definition(count, verbose=True)
    before = base.model_dump(mode="json")
    provider = StageProvider(("prepare_changes", {"criterion_ids": [], "item_ids": ["phone"]}),
                             ("propose_changes", addition()))
    agent = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider, definition=base)
    result = agent.ask("再给审核指标加上一个交期吧")
    assert result["status"] == "completed", result["error"]
    candidate = result["payload"]["candidate"]
    assert len(candidate["criteria"]) == count + 1
    assert candidate["criteria"][:-1] == before["criteria"]
    assert result["usage"]["model_calls"] == 2
    assert base.model_dump(mode="json") == before
    for messages, schemas in provider.requests:
        assert input_bound(messages, schemas) <= 16000
    assert "propose_changes" not in {s["function"]["name"] for s in provider.requests[0][1]}
    assert {s["function"]["name"] for s in provider.requests[1][1]} == {"propose_changes", "chat"}
    assert result["context"]["edit_requests"] == 1
    assert result["context"]["max_request_bound"] <= 16000


def test_large_checklist_can_page_to_and_update_a_late_target_without_loading_other_rules():
    base = definition(100, verbose=True)
    target = base.criteria[-1].model_dump(mode="json")
    edit = {k: v for k, v in target.items() if k not in {"source", "criterion_id"}}
    edit["rule"] = {"type": "manual", "instruction": "明确核对订单确认后交付的承诺"}
    provider = StageProvider(("read_context", {"query": "审查要求99", "offset": 0, "limit": 8}),
        ("prepare_changes", {"criterion_ids": [target["criterion_id"]], "item_ids": []}),
        ("propose_changes", {"items": [], "operations": [{"op": "update", "criterion_id": target["criterion_id"],
            "criterion": edit, "reason": "买方明确调整"}]}))
    result = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider, definition=base).ask("修改审查要求99")
    assert result["status"] == "completed", result["error"]
    assert result["payload"]["candidate"]["criteria"][:-1] == base.model_dump(mode="json")["criteria"][:-1]
    assert result["payload"]["candidate"]["criteria"][-1]["conditions"] == target["conditions"]
    assert result["context"]["catalog_reads"] == 1
    for messages, schemas in provider.requests:
        assert input_bound(messages, schemas) <= 16000
    editor_system = provider.requests[-1][0][0].content
    assert "criterion-99" in editor_system and "criterion-50" not in editor_system


def test_editor_cannot_update_a_valid_but_unprepared_target():
    base = definition(2)
    target = base.criteria[-1].model_dump(mode="json")
    edit = {k: v for k, v in target.items() if k not in {"source", "criterion_id"}}
    provider = StageProvider(("prepare_changes", {"criterion_ids": ["criterion-0"], "item_ids": []}),
        ("propose_changes", {"items": [], "operations": [{"op": "update", "criterion_id": "criterion-1", "criterion": edit, "reason": "越界修改"}]}))
    result = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider, definition=base).ask("只修改第一项")
    assert result["status"] == "partial" and result["payload"]["candidate"] is None
    assert result["usage"]["model_calls"] == 2


def test_extreme_single_target_has_an_actionable_scope_limit_without_clipping_it():
    base = definition(1).model_dump(mode="json")
    base["criteria"][0]["rule"] = {"type": "enum", "operator": "in", "values": [f"选项{i}" + "非常详细的限定内容" * 10 for i in range(32)]}
    original = deepcopy(base)
    provider = StageProvider(("prepare_changes", {"criterion_ids": ["criterion-0"], "item_ids": []}))
    result = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider, definition=base).ask("修改这项枚举条件")
    assert result["status"] == "waiting_user" and result["error"] == "checklist_edit_scope_budget"
    assert "编辑" in result["answer"] and "保留" in result["answer"]
    assert result["payload"]["candidate"] is None and result["usage"]["model_calls"] == 1
    assert base == original


def test_runtime_beijing_date_comes_from_current_clock_not_old_chat():
    provider = StageProvider(("chat", {"answer": "今天是2026年10月9日。"}))
    result = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider,
        clock=lambda: datetime(2026, 10, 8, 16, 30, tzinfo=UTC)).ask("今天几号")
    assert result["status"] == "completed"
    assert '2026-10-09' in result['answer'] and '北京时间' in result['answer']
    assert not provider.requests and result['usage']['model_calls'] == 0


@pytest.mark.parametrize("selection", [
    {"criterion_ids": ["missing"], "item_ids": []},
    {"criterion_ids": [], "item_ids": ["other-case-item"]},
    {"criterion_ids": ["criterion-0", "criterion-0"], "item_ids": []},
    {"criterion_ids": [f"criterion-{i}" for i in range(5)], "item_ids": []},
])
def test_invalid_preparation_never_exposes_editor_or_creates_candidate(selection):
    provider = StageProvider(("prepare_changes", selection))
    result = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider, definition=definition(6)).ask("修改指标")
    assert result["status"] == "partial" and result["payload"]["candidate"] is None
    assert result["usage"]["model_calls"] == 1


def test_catalog_pages_replace_previous_page_and_do_not_load_rules():
    provider = StageProvider(("read_context", {"offset": 8}),
        ("read_context", {"offset": 16}), ("chat", {"answer": "已查看后面的目录。"}))
    result = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider, definition=definition(40, verbose=True)).ask("查看后面的指标")
    assert result["status"] == "completed" and result["context"]["catalog_reads"] == 2
    last = provider.requests[-1][0]
    assert len(last) == 4  # system/current question/latest call/latest observation
    assert '"offset":16' in last[0].content and '"next_offset":24' in last[0].content
    assert "供应商应提供详细采购验收说明" not in last[0].content
    assert all(input_bound(m, s) <= 16000 for m, s in provider.requests)


@pytest.mark.parametrize("question_length", [10, 1000, 2000])
def test_long_names_across_twenty_items_still_allow_chat_and_lookup_of_last_item(question_length):
    base = definition(100).model_dump(mode="json")
    base["items"] = [{"item_id": f"item-{i}", "name": f"品目{i}" + "采购品目详细名称" * 12} for i in range(20)]
    for i, criterion in enumerate(base["criteria"]):
        criterion["item_id"] = f"item-{i % 20}"
        criterion["name"] = f"要求{i}" + "详细核验方向" * 15
    provider = StageProvider(("read_context", {"query": "item-19"}), ("chat", {"answer": "已找到最后一个品目。"}))
    result = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider, definition=base).ask("查" * question_length)
    assert result["status"] == "completed", result["error"]
    assert '"item_id":"item-19"' in provider.requests[-1][0][0].content
    assert all(input_bound(m, s) <= 16000 for m, s in provider.requests)


def test_item_catalog_can_enumerate_late_items_even_when_they_have_no_criteria():
    from backend.app.agent.assistant_checklist_context import ReadChecklistArgs, catalog

    base = definition(1).model_dump(mode="json")
    base["items"] += [{"item_id": f"unused-{i}", "name": f"暂无指标品目{i}"} for i in range(19)]
    base = ChecklistDefinition.model_validate(base)
    found, offset = [], 0
    while True:
        page = catalog(base, ReadChecklistArgs(scope="items", offset=offset))
        found.extend(i["item_id"] for i in page["items"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert found == [i.item_id for i in base.items]


def test_cancellation_between_preparation_and_edit_stops_before_second_model_call():
    from backend.app.agent.policy import RunStopped

    provider = StageProvider(("prepare_changes", {"criterion_ids": [], "item_ids": []}))

    def validate():
        if provider.requests:
            raise RunStopped("cancelled")

    result = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider,
        definition=definition(10), validate=validate).ask("补充交期")
    assert result["status"] == "cancelled" and result["usage"]["model_calls"] == 1
    assert result["payload"]["candidate"] is None


def test_date_refreshes_on_each_question_after_beijing_midnight():
    instants = iter([datetime(2026, 10, 8, 15, 59, tzinfo=UTC), datetime(2026, 10, 8, 16, 1, tzinfo=UTC)])
    provider = StageProvider(("chat", {"answer": "8号"}), ("chat", {"answer": "9号"}))
    agent = AssistantAgent(EvidenceRepository.empty("case", "snapshot"), provider, clock=lambda: next(instants))
    assert '2026-10-08' in agent.ask("今天几号")["answer"]
    assert '2026-10-09' in agent.ask("今天几号")["answer"]
    assert not provider.requests
