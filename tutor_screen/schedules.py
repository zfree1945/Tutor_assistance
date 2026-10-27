"""保留排除窗口，数字钟点只在可确定时转换为现有粗粒度时段。"""
import re
from .models import ScheduleWindow

SLOT_WORDS = {"上午": ["上午", "早上", "早晨"], "下午": ["下午", "午后"], "晚上": ["晚上", "晚间", "夜间", "傍晚"]}
CLOCK_RANGE = re.compile(r"(?<!\d)(\d{1,2})[:：](\d{2})\s*(?:[-~～—至到])\s*(\d{1,2})[:：](\d{2})(?!\d)")
TIME_HINT = re.compile(r"上午|下午|晚上|早上|晚\d|\d\s*(?:点|[:：]\d)|(?:周|星期|礼拜)[一二三四五六日天1-7]")
NEGATIVE = re.compile(r"除了|除外|不行|不可以|不能|不要安排|不方便|没空|不可|不接受|不能安排")
PERIOD = r"上午|早上|早晨|中午|下午|午后|晚上|晚间|夜间|傍晚|晚"
CN_DIGITS = "零〇一二两三四五六七八九十"
HOUR = rf"(?:\d{{1,3}}|[{CN_DIGITS}]{{1,3}})"
FLEXIBLE = re.compile(r"可协商|可协调|时间协调|任选|任意|之间|待定|待确认|不固定|看情况|都可以|至少|最多|不超过")
NON_WEEKLY = re.compile(r"(?:每|隔)\s*[\d一二两三四五六七八九十]+\s*(?:周|星期|礼拜)|隔周|单周|双周|每月|两周|2周|下周|本周|这周|或者|或|任选")
CLAUSE_SEPARATOR = r"[，,；;。\r\n]|但是|但|不过|然而"
MULTIPLE_LESSONS = re.compile(r"(?:每天|每日|一天)\s*[\d一二两三四五六七八九十]+\s*(?:节|次)")


def _endpoint(prefix: str, optional_unit: bool = False) -> str:
    unit = rf"(?:[:：]\s*(?P<{prefix}_minute>\d{{2}})|点(?:钟)?\s*(?:(?P<{prefix}_half>半)|(?P<{prefix}_point_minute>\d{{1,3}}|[{CN_DIGITS}]{{1,3}})\s*分)?)"
    return rf"(?P<{prefix}_period>{PERIOD})?\s*(?P<{prefix}_hour>{HOUR})\s*(?P<{prefix}_unit>{unit})" + ("?" if optional_unit else "")


CLOCK_WINDOW = re.compile(rf"(?<!\d){_endpoint('start', True)}\s*(?:[-~～—至到])\s*{_endpoint('end')}(?!\d)")


def _integer(token: str) -> int:
    if token.isdigit():
        return int(token)
    digits = {character: index for index, character in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    if "十" in token:
        tens, units = token.split("十", 1)
        return (digits[tens] if tens else 1) * 10 + (digits[units] if units else 0)
    return digits[token]


def _clock_minutes(match, prefix: str, inherited_period: str | None = None) -> int | None:
    try:
        hour = _integer(match[f"{prefix}_hour"])
        minute = 30 if match[f"{prefix}_half"] else _integer(match[f"{prefix}_minute"] or match[f"{prefix}_point_minute"] or "0")
    except (ValueError, KeyError):
        return None
    if hour > 24 or minute > 59 or hour == 24 and minute:
        return None
    period = match[f"{prefix}_period"] or inherited_period
    if period in {"下午", "午后", "晚上", "晚间", "夜间", "傍晚", "晚"}:
        if hour < 12:
            hour += 12
        elif hour == 12 and period not in {"下午", "午后"}:
            hour = 24 if prefix == "end" else 0
    elif period in {"上午", "早上", "早晨"}:
        if hour > 12:
            return None
    elif period == "中午":
        if hour < 6:
            hour += 12
    elif ":" not in (match[f"{prefix}_unit"] or "") and "：" not in (match[f"{prefix}_unit"] or "") and hour < 12:
        # “1点到3点”缺少上下午；24小时冒号写法不需要此推断。
        return None
    return hour * 60 + minute


def _positive_text(text: str) -> tuple[str, list[str]]:
    positive, excluded = [], []
    for clause in re.split(CLAUSE_SEPARATOR, text):
        if TIME_HINT.search(clause) and NEGATIVE.search(clause):
            excluded.append(clause.strip())
        else:
            positive.append(clause)
    return "，".join(positive), excluded


def clock_windows(text: str, read_days=lambda _: []) -> tuple[list[ScheduleWindow], bool]:
    positive_text, _ = _positive_text(text)
    windows = []
    unclear = False
    previous_end = 0
    previous_days = []
    for match in CLOCK_WINDOW.finditer(positive_text):
        start = _clock_minutes(match, "start")
        end = _clock_minutes(match, "end", match["start_period"])
        context = positive_text[previous_end:match.start()]
        days = read_days(context) or previous_days
        previous_end = match.end()
        previous_days = days
        if start is None or end is None or start < 360 or start >= 1440 or end <= start:
            unclear = True
            continue
        windows.append(ScheduleWindow(days=days, start_minute=start, end_minute=end,
                                      source=(context + match.group()).strip(" ，,；;")))
    return windows, unclear


def inferred_weekly_sessions(text: str, days: list[int], windows: list[ScheduleWindow], unclear: bool) -> int | None:
    if not windows or unclear or FLEXIBLE.search(text) or NON_WEEKLY.search(text) or NEGATIVE.search(text) or MULTIPLE_LESSONS.search(text):
        return None
    scheduled = [day for window in windows for day in window.days]
    if not days or set(scheduled) != set(days) or len(scheduled) != len(set(scheduled)):
        return None
    return len(days)


def read_time_details(text: str) -> tuple[list[str], list[str], bool]:
    positive_text, excluded = _positive_text(text)
    windows, unclear_clock = clock_windows(positive_text)
    remaining = CLOCK_WINDOW.sub("", positive_text)
    slots = {slot for slot, words in SLOT_WORDS.items() if any(word in remaining for word in words)}
    if re.search(r"晚\s*\d", remaining):
        slots.add("晚上")
    for window in windows:
        start, end = window.start_minute, window.end_minute
        for slot, low, high in [("上午", 360, 720), ("下午", 720, 1080), ("晚上", 1080, 1440)]:
            if max(start, low) < min(end, high):
                slots.add(slot)
    if unclear_clock:
        slots.clear()
    return [slot for slot in SLOT_WORDS if slot in slots], excluded, unclear_clock
