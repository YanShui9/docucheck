"""Read-only answers bound to an immutable confirmed checklist, never an edit base."""

import re
from typing import Literal

from pydantic import Field, model_validator

from ..criteria.models import ChecklistDefinition, Contract, Identifier
from ..evidence.repository import digest
from .assistant_checklist_context import ReadChecklistArgs, catalog, model_definition


class ReadConfirmedArgs(Contract):
    query: str = Field(default="", max_length=120)
    offset: int = Field(default=0, ge=0, le=100)
    limit: int = Field(default=4, ge=1, le=4)


class ChecklistAnswerArgs(Contract):
    answer: str = Field(min_length=1, max_length=600, pattern=r"\S")
    criterion_ids: list[Identifier] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_refs(self):
        if len(set(self.criterion_ids)) != len(self.criterion_ids):
            raise ValueError("checklist_answer_reference")
        return self


class ChecklistAnswer(Contract):
    version: Literal[1]
    text: str = Field(min_length=1, max_length=600, pattern=r"\S")
    checklist_version_id: str = Field(min_length=1, max_length=64)
    definition_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    criteria: list[dict] = Field(min_length=1, max_length=4)


def read_confirmed(definition: ChecklistDefinition, query: ReadConfirmedArgs):
    page = catalog(definition, ReadChecklistArgs(query=query.query, offset=query.offset, limit=query.limit),
                   combined=True)
    ids = {c["criterion_id"] for c in page["criteria"]}
    items = {i.item_id: i.model_dump(mode="json") for i in definition.items}
    page.update(view="confirmed_rules", confirmed=True)
    page["criteria"] = [c | {"item": items[c["item_id"]]} for c in model_definition(definition)["criteria"]
                        if c["criterion_id"] in ids]
    return page


def prepare_checklist_answer(args, definition, version_id, read):
    if definition is None or not version_id or not set(args.criterion_ids) <= read.keys():
        raise ValueError("checklist_answer_unread")
    text = args.answer.strip()
    display_refs = {ref: str(index) for index, ref in enumerate(args.criterion_ids, 1) if not ref.isdecimal()}
    text = re.sub(r"\[([\w-]+)\]", lambda match: "[" + display_refs.get(match[1], match[1]) + "]", text)
    for ref in [*args.criterion_ids, version_id]:
        if len(ref) >= 16 and re.search(r"(?<![\w-])" + re.escape(ref) + r"(?![\w-])", text):
            raise ValueError("checklist_answer_reference")
    if re.search(r"[a-fA-F0-9]{32,64}", text):
        raise ValueError("checklist_answer_reference")
    return ChecklistAnswer(version=1, text=text, checklist_version_id=version_id,
        definition_digest=digest(definition.model_dump(mode="json")),
        criteria=[read[ref] for ref in args.criterion_ids]).model_dump(mode="json")


def checklist_answer_digest(payload):
    value = payload.get("checklist_answer")
    return digest(value) if value is not None else None


def render_checklist_answer(value, definition, version_id):
    answer = ChecklistAnswer.model_validate(value)
    if definition is None or answer.checklist_version_id != version_id:
        raise ValueError("checklist_answer_changed")
    refs = [c.get("criterion_id") for c in answer.criteria]
    full = {c["criterion_id"]: c for offset in range(0, len(definition.criteria), 4)
            for c in read_confirmed(definition, ReadConfirmedArgs(offset=offset))["criteria"]}
    expected = prepare_checklist_answer(ChecklistAnswerArgs(answer=answer.text, criterion_ids=refs),
                                         definition, version_id, full)
    if expected != answer.model_dump(mode="json"):
        raise ValueError("checklist_answer_changed")
    sources = []
    for c in answer.criteria:
        flags = ["必选" if c["required"] else "可选", "已启用" if c["enabled"] else "已停用"]
        if c["rule"]["type"] == "manual":
            flags.append("需人工核对")
        if c["conditions"]:
            flags.append("适用条件：" + "；".join(c["conditions"]))
        sources.append(f"{c['item']['name']} · {c['name']}（{'，'.join(flags)}）")
    return answer.text + "\n依据：已确认审查清单 · " + "；".join(sources) + "。"
