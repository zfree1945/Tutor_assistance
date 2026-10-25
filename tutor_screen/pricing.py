"""本地换算课费；粘连报价优先使用原文时长，否则按单次不超过9小时拆分。"""

import re
import math
from dataclasses import dataclass

from .models import ExtractedOrder
from .text_fields import read_fields


NUMBER = r"\d+(?:\.\d+)?"
AMOUNT = rf"{NUMBER}(?:\s*[-~～—至到]\s*{NUMBER})?"
CN_DURATION = r"(?:[一二两三四五六七八九十]{1,3}(?:\s*个?\s*半)?|半)"
DURATION = rf"(?:{NUMBER}|{CN_DURATION})"
HOURS_UNIT = r"(?:小时|钟头|时|h|分钟|min)"
TAIL = r"(?P<tail>半|[一二两三四五六七八九十\d]+\s*分钟)?"
CN_NUMBERS = {"半": 0.5, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
CONTEXT_DURATION = re.compile(rf"(?:每次(?:课)?|一次(?:课)?|每节(?:课)?|每课时)\s*[:：]?\s*({DURATION})\s*个?\s*(?P<unit>小时|钟头|h|分钟|min)(?P<tail>半|[一二两三四五六七八九十\d]+\s*分钟)?", re.I)
PERIOD_DURATION = re.compile(rf"(?:上午|早上|早晨|中午|下午|午后|晚上|晚间)\s*({DURATION})\s*个?\s*(?P<unit>小时|钟头|h|分钟|min){TAIL}", re.I)
RATE_PATTERN = re.compile(rf"(?:(?:时薪|每小时|小时费)\s*[:：]?\s*)?(?<![\d.])(?P<amount>{AMOUNT})\s*(?:元|块)?\s*[/／]\s*(?P<duration>{DURATION})?\s*个?\s*(?P<unit>{HOURS_UNIT}){TAIL}", re.I)
HOURLY_LABEL = re.compile(rf"(?:时薪|每小时|小时费)\s*[:：]?\s*(?P<amount>{AMOUNT})")
SEPARATED_PATTERN = re.compile(rf"(?<![\d.])(?P<amount>{AMOUNT})(?:\s*(?:元|块)\s*|\s+)(?P<duration>{DURATION})\s*个?\s*(?P<unit>{HOURS_UNIT}){TAIL}", re.I)
NATURAL_RATE = re.compile(rf"(?<![\d.])(?P<amount>{AMOUNT})\s*(?:元|块)?\s*(?:每)?(?P<duration>{CN_DURATION})\s*个?\s*(?P<unit>小时|钟头){TAIL}")
PER_HOUR = re.compile(rf"(?<![\d.])(?P<amount>{AMOUNT})\s*(?:元|块)?\s*每\s*小时")
GLUED_PATTERN = re.compile(r"(?<![\d.\-−~～—至到/／])(?P<combined>\d{2,7}(?:\.\d+)?(?:\s*[-~～—至到]\s*\d{2,7}(?:\.\d+)?)?)\s*(?:小时|h)(?![a-z])", re.I)
UNKNOWN_PATTERN = re.compile(rf"面议|{AMOUNT}\s*元?\s*/\s*课时")
INVALID_PATTERN = re.compile(rf"(?<![\d.])(?:[-−]\s*{AMOUNT}\s*(?:元|块)?\s*[/／]\s*{DURATION}?\s*{HOURS_UNIT}|{AMOUNT}\s*(?:元|块)?\s*[/／]\s*[-−]\s*{DURATION}\s*{HOURS_UNIT})", re.I)
PRICE_UNCERTAINTY = {"时薪", "时薪范围", "部分薪资", "课费", "课时费", "hourly_rate", "price_text", "hourly_rate_min", "hourly_rate_max"}


@dataclass
class Price:
    raw_text: str | None = None
    lower: float | None = None
    upper: float | None = None
    evidence: str = ""
    has_unknown: bool = False

    @property
    def hourly_rate(self) -> float | None:
        return self.lower if not self.has_unknown and self.lower is not None and self.lower == self.upper else None


def _number(value: str) -> float:
    value = re.sub(r"\s|个", "", value)
    if value.endswith("半") and value != "半":
        return _number(value[:-1]) + 0.5
    if value in CN_NUMBERS:
        return float(CN_NUMBERS[value])
    if "十" in value:
        tens, units = value.split("十", 1)
        return (CN_NUMBERS[tens] if tens else 1) * 10 + (CN_NUMBERS[units] if units else 0)
    return float(value)


def _duration_value(match: re.Match, value: str) -> float:
    hours = _number(value)
    unit = match.groupdict().get("unit", "").lower()
    if unit in {"分钟", "min"}:
        hours /= 60
    tail = match.groupdict().get("tail")
    if tail and unit not in {"分钟", "min"}:
        hours += 0.5 if tail == "半" else _number(tail.removesuffix("分钟").strip()) / 60
    return hours


def _bounds(amount: str, duration: float) -> tuple[float | None, float | None]:
    if duration <= 0:
        return None, None
    values = [float(value) for value in re.findall(NUMBER, amount)]
    if len(values) == 2 and values[0] > values[1]:
        return None, None
    lower, upper = values[0] / duration, values[-1] / duration
    if not all(math.isfinite(value) for value in (duration, lower, upper)):
        return None, None
    return round(lower, 2), round(upper, 2)


def _lesson_durations(text: str) -> set[float]:
    values = {_duration_value(match, match.group(1)) for match in CONTEXT_DURATION.finditer(text)}
    # “周六下午四小时”也是时长证据；只在时间字段或无标题文本中读取，
    # 避免把薪资里的“下午1802h”误当成课时，或把14h钟点当成14小时。
    fields = read_fields(text)
    schedules = [field.value for field in fields if field.category == "schedule"]
    for schedule in schedules or ([text] if not fields else []):
        values.update(hours for match in PERIOD_DURATION.finditer(schedule)
                      if 0 < (hours := _duration_value(match, match.group(1))) <= 9)
    return values


def lesson_duration(text: str) -> float | None:
    values = _lesson_durations(text)
    return next(iter(values)) if len(values) == 1 and math.isfinite(next(iter(values))) and next(iter(values)) > 0 else None


def _compact_price(match: re.Match, durations: set[float]) -> Price:
    combined = match.group("combined")
    tokens = re.findall(NUMBER, combined)
    last = tokens[-1]
    price = Price(raw_text=match.group(), evidence="价格与课时长度粘连，时长证据冲突或无法拆分")
    duration = next(iter(durations)) if len(durations) == 1 else None
    if duration and math.isfinite(duration) and duration > 0:
        suffix = f"{duration:g}"
        # 有独立证据时也接受超过9小时的明确安排；9小时仅是缺省拆分规则。
        if last.endswith(suffix) and len(last) > len(suffix):
            amount = last[:-len(suffix)]
            if amount.isdigit() and len(amount) >= 2:
                bounds = combined[:combined.rfind(last)] + amount
                price.lower, price.upper = _bounds(bounds, duration)
                price.evidence = f"{match.group()}：按原文每次 {duration:g} 小时补全分隔符并换算"
                return price
    # 两位数和以0结尾的整数不拆成“金额/0小时”，按省略斜线的小时报价处理。
    # 有独立时长证据时已在上方优先拆分，例如每次10小时的18010h。
    integer = last.split(".")[0]
    if len(integer) <= 2 or integer.endswith("0"):
        price.lower, price.upper = _bounds(combined, 1)
        price.evidence = f"{match.group()}：按报价中的每小时简写换算"
        return price
    if durations:
        return price

    split = re.fullmatch(r"(?P<amount>\d{2,6})(?P<hours>[1-9](?:\.\d+)?)", last)
    if split and 0 < float(split["hours"]) <= 9:
        duration = float(split["hours"])
        amount = combined[:combined.rfind(last)] + split["amount"]
        price.lower, price.upper = _bounds(amount, duration)
        price.evidence = f"{match.group()}：按单次不超过9小时，将末尾 {duration:g} 小时补全分隔符并换算"
    return price


def parse_price(text: str) -> Price:
    candidates: list[tuple[int, int, Price]] = []
    fields = read_fields(text)
    for pattern, default_duration in [(RATE_PATTERN, 1.0), (HOURLY_LABEL, 1.0), (SEPARATED_PATTERN, None), (NATURAL_RATE, None), (PER_HOUR, 1.0)]:
        for match in pattern.finditer(text):
            # 科目中的“70-75/100\n时间安排”不能跨字段截成“70-75/100时”。
            if any(match.start() < field.value_end < match.end() for field in fields):
                continue
            # 不允许从负数中截取正数尾部，负报价也必须留下未知证据。
            if match.start() and text[match.start() - 1] in "-−":
                candidates.append((match.start() - 1, match.end(), Price(text[match.start() - 1:match.end()], evidence="负报价无法确定时薪", has_unknown=True)))
                continue
            duration_text = match.groupdict().get("duration")
            duration = _duration_value(match, duration_text) if duration_text else default_duration
            if duration is None:
                continue
            lower, upper = _bounds(match.group("amount"), duration)
            evidence = f"{match.group()}：课费除以 {duration:g} 小时"
            candidates.append((match.start(), match.end(), Price(match.group(), lower, upper, evidence)))

    durations = _lesson_durations(text)
    for match in GLUED_PATTERN.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end, _ in candidates):
            continue
        containing = [field for field in fields if field.value_start <= match.start() < field.value_end]
        if containing and containing[0].category != "fee":
            continue
        prefix = text[:match.start()]
        negative = re.search(r"[-−]\s*$", prefix)
        if negative:
            candidates.append((negative.start(), match.end(), Price(text[negative.start():match.end()], evidence="负报价无法确定时薪", has_unknown=True)))
            continue
        # 无报价标题时，也不把“每次12h”“累计180h”等时长当成薪资。
        if re.search(r"(?:每次(?:课)?|一次(?:课)?|每节(?:课)?|每课时|共|总计|累计|已学|用时|时长|每天|每周|每月|上午|下午|晚上)\s*[:：]?\s*$", prefix):
            continue
        if not containing and re.fullmatch(NUMBER, match["combined"]) and float(match["combined"]) <= 24:
            continue
        candidates.append((match.start(), match.end(), _compact_price(match, durations)))

    for match in UNKNOWN_PATTERN.finditer(text):
        candidates.append((match.start(), match.end(), Price(match.group(), evidence="课费无法确定换算为时薪")))
    for match in INVALID_PATTERN.finditer(text):
        candidates.append((match.start(), match.end(), Price(match.group(), evidence="负报价或负课时无法确定时薪", has_unknown=True)))
    for field in fields:
        if field.category == "fee" and field.value and not any(start < field.value_end and end > field.value_start for start, end, _ in candidates):
            candidates.append((field.value_start, field.value_end, Price(field.value, evidence="本项课费缺少可验证的时薪换算证据", has_unknown=True)))
    if not candidates:
        fees = [field.value for field in fields if field.category == "fee" and field.value]
        if fees:
            return Price(raw_text="；".join(fees), evidence="原文课费缺少可验证的时薪换算证据", has_unknown=True)
        return Price()
    # 相互重叠的匹配只保留完整表达式，避免把区间尾部或 /2h 当成另一个价格。
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, Price]] = []
    for candidate in candidates:
        if not selected or candidate[0] >= selected[-1][1]:
            selected.append(candidate)
    primary = selected[0][2]
    if len(selected) == 1:
        return primary
    # 多个价格（如后续涨价）不能默认为当前价格；保留完整范围供本地筛选。
    known = [price for _, _, price in selected if price.lower is not None and price.upper is not None]
    return Price(
        raw_text="；".join(price.raw_text for _, _, price in selected),
        lower=min(price.lower for price in known) if known else None,
        upper=max(price.upper for price in known) if known else None,
        evidence="；".join(price.evidence for _, _, price in selected),
        has_unknown=len(known) < len(selected),
    )


def normalize_price(order: ExtractedOrder, text: str) -> None:
    price = parse_price(text)
    if price.raw_text is None:
        return
    order.hourly_rate = price.hourly_rate
    order.hourly_rate_min = price.lower
    order.hourly_rate_max = price.upper
    order.price_incomplete = price.has_unknown or price.lower is None or price.upper is None
    order.price_text = price.raw_text
    order.field_sources["hourly_rate"] = price.evidence
    order.uncertain_fields = [field for field in order.uncertain_fields if field not in PRICE_UNCERTAINTY]
    if price.lower is None or price.upper is None:
        order.uncertain_fields.append("时薪")
    elif order.price_incomplete:
        order.uncertain_fields.append("部分薪资")
