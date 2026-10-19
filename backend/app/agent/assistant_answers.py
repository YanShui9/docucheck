"""Display model summaries beside verified citations; never make formal findings."""

import re
from typing import Literal

from pydantic import Field

from ..criteria.models import Contract
from ..evidence.repository import digest

NO_EVIDENCE_ANSWER = "本次检索未找到可直接回答问题的原文。"


class EvidenceAnswer(Contract):
    version: Literal[1]
    text: str = Field(min_length=1, max_length=1200, pattern=r"\S")


def prepare_answer(text: str, citations: list[dict]) -> EvidenceAnswer:
    if not citations:
        return EvidenceAnswer(version=1, text=NO_EVIDENCE_ANSWER)
    text = text.strip()
    for index, citation in enumerate(citations, 1):
        ref = citation["evidence_id"]
        text = text.replace(f"[{ref}]", f"[{index}]").replace(ref, f"[{index}]")
    if re.search(r"[a-fA-F0-9]{64}", text):
        raise ValueError("evidence_answer_reference")
    return EvidenceAnswer(version=1, text=text)


def answer_digest(payload: dict) -> str | None:
    if payload.get("evidence_answer") is None:
        return None
    return digest({key: payload[key] for key in ("evidence_answer", "citations", "retrieval")})


def render_answer(answer: dict | EvidenceAnswer, citations: list[dict], retrieval: dict | None = None) -> str:
    parsed = EvidenceAnswer.model_validate(answer)
    # Reapply display/no-evidence guards when loading persisted results too.
    if prepare_answer(parsed.text, citations) != parsed:
        raise ValueError("evidence_answer_changed")
    text = parsed.text
    if retrieval and retrieval.get("degraded_reason"):
        notices = {
            "item_binding_not_confirmed": "请先确认资料与品目的对应；本次没有扩大到其他品目的资料。",
            "vector_item_scope_unrepresentable": "本次在已确认资料范围内使用关键词检索。",
            "vector_item_scope_budget": "本次在已确认资料范围内使用关键词检索。",
        }
        text += "\n" + notices.get(retrieval["degraded_reason"], "本次使用关键词检索，语义检索暂不可用。")
    return text
