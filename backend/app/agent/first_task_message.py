"""Render original dialogue without a second model paraphrasing it."""

from datetime import datetime, timedelta

from pydantic import Field

from ..criteria.models import Contract


class FirstTaskMessageArgs(Contract):
    start_char: int = Field(default=0, ge=0, le=2000, description="首段为0；继续时从上段末尾的下一字符开始，按0计数")


def render_first_task_message(page):
    if page is None:
        return "本次提问之前，当前采购任务中没有可定位的用户消息。"
    moment = datetime.fromisoformat(page["created_at"]) + timedelta(hours=8)
    result = f"原话：\n{page['text']}\n\n来源：对话 {page['conversation_number']} · {moment:%Y-%m-%d %H:%M:%S}（北京时间） · 当时资料版本 {page['revision']}。"
    if page["start_char"] or page["next_char"] is not None:
        end = page["start_char"] + len(page["text"])
        result += f"\n本段为第{page['start_char'] + 1}–{end}字，共{page['total_chars']}字。"
        if page["next_char"] is not None:
            result += f"可要求从第{end + 1}字继续回读。"
    return result
