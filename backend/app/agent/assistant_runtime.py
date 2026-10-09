"""Runtime facts are obtained only when requested, never as ambient prompt data."""
import re
from datetime import timedelta, timezone


def clock_question(question):
    text = re.sub(r"[\s，,。.!！?？]", "", question)
    if text in {"今天几号", "今天是几号", "今天多少号", "今天几月几号", "今天是什么日期", "今天的日期"}:
        return "date"
    if text in {"今天星期几", "今天是星期几", "今天周几"}:
        return "weekday"
    if text in {"现在几点", "现在几点了", "现在是什么时间"}:
        return "time"
    return None


def read_clock(clock):
    instant = clock()
    if instant.tzinfo is None:
        raise ValueError("runtime_clock_timezone")
    local = instant.astimezone(timezone(timedelta(hours=8)))
    return {"date": local.date().isoformat(), "time": local.strftime("%H:%M:%S"),
            "weekday": "星期" + "一二三四五六日"[local.weekday()], "timezone": "Asia/Shanghai"}
