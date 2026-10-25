"""本地确定性筛选：返回 None 表示条件明确不符。"""

from .models import Criteria, Order
import re

from .local_parser import DAY, DAY_RANGE, _read_days, normalize_local_fields
from .schedules import SLOT_WORDS


GROUPS = {
    "理科": {"物理", "化学", "生物", "科学", "技术"},
    "文科": {"历史", "政治", "地理"},
}


def _subject_match(order_subjects: list[str], wanted: list[str]) -> tuple[bool, bool]:
    if not wanted:
        return True, False
    for subject in order_subjects:
        if subject in wanted:
            return True, False
        if any(subject in GROUPS.get(w, set()) for w in wanted):
            return True, False
        if subject in GROUPS and any(w in GROUPS[subject] for w in wanted):
            return True, True
        if subject in {"全科", "数理化"}:
            return True, True
    return (True, True) if not order_subjects else (False, False)


def _exclude(order: Order, reason: str) -> Order:
    order.match_status = "已排除"
    order.match_reason = reason
    return order


def evaluate_order(order: Order, criteria: Criteria) -> Order:
    # 从原文修正会话里的旧结果，应用筛选无需再次请求模型。
    order = Order.model_validate(order.model_dump())
    try:
        normalize_local_fields(order, order.raw_text)
    except Exception:
        order.uncertain_fields = sorted(set(order.uncertain_fields) | {"本地解析"})
    reasons: list[str] = []
    uncertain = set(order.uncertain_fields)
    price_known = order.hourly_rate is not None or order.hourly_rate_min is not None and order.hourly_rate_max is not None
    no_core_fields = not order.grade and not order.subjects and not price_known and order.mode == "未知"

    if not order.grade:
        uncertain.add("年级")
    if not order.subjects or any(s in {"全科", "数理化", "理科", "文科"} for s in order.subjects):
        uncertain.add("科目")
    if not price_known:
        uncertain.add("时薪")
    if order.mode == "未知":
        uncertain.add("上课方式")
    if not order.days:
        uncertain.add("星期")

    if criteria.teacher_gender != "不限":
        if order.teacher_gender in {"男", "女"} and order.teacher_gender != criteria.teacher_gender:
            return _exclude(order, "教师性别要求与当前条件不符")
        if order.teacher_gender == "未知":
            uncertain.add("教师性别要求")
    if criteria.grades:
        if order.grade and order.grade not in criteria.grades:
            return _exclude(order, "年级不在当前可接范围内")
        if not order.grade:
            uncertain.add("年级")
    subject_ok, subject_uncertain = _subject_match(order.subjects, criteria.subjects)
    if not subject_ok:
        return _exclude(order, "科目不在当前可接范围内")
    if subject_uncertain:
        uncertain.add("科目")
    if criteria.mode != "不限":
        if order.mode not in {criteria.mode, "线上/线下均可", "未知"}:
            return _exclude(order, "上课方式与当前条件不符")
        if order.mode == "未知":
            uncertain.add("上课方式")
    if criteria.days and set(criteria.days).issubset(order.excluded_days):
        return _exclude(order, "可接星期全部属于订单明确排除的星期")
    if criteria.time_slots and order.excluded_times:
        blocked = set()
        for clause in order.excluded_times:
            # 精确钟点只排除窗口，不能扩大成整段上午/下午/晚上。
            clock_context = DAY.sub("", DAY_RANGE.sub("", clause))
            if re.search(r"\d+\s*[:：]|\d+\s*(?:[-~～至到]|点)|[一二三四五六七八九十]+点", clock_context):
                continue
            for day in _read_days(clause):
                for slot, aliases in SLOT_WORDS.items():
                    if any(alias in clause for alias in aliases):
                        blocked.add((day, slot))
        fixed_days = order.days if not order.day_options and not order.schedule_options else []
        if any(all((day, slot) in blocked for slot in criteria.time_slots) for day in fixed_days):
            return _exclude(order, "固定上课日的可接时段与订单排除时段冲突")
        if not fixed_days and criteria.days and all((day, slot) in blocked or day in order.excluded_days for day in criteria.days for slot in criteria.time_slots):
            return _exclude(order, "可接日期和时段全部被订单排除")
    if order.schedule_options and (criteria.days or criteria.time_slots):
        options = [option for option in order.schedule_options
                   if (not criteria.days or set(option.days).issubset(criteria.days))
                   and (not criteria.time_slots or not option.time_slots or set(option.time_slots).issubset(criteria.time_slots))]
        if not options:
            return _exclude(order, "没有可接的完整备选上课安排")
        if criteria.time_slots and not any(option.time_slots for option in options):
            uncertain.add("时间段")
    elif criteria.days:
        if order.day_options:
            if not any(set(option).issubset(criteria.days) for option in order.day_options):
                return _exclude(order, "没有符合可接星期的备选安排")
        elif order.days and not set(order.days).issubset(criteria.days):
            return _exclude(order, "无法覆盖订单全部固定上课日")
        if not order.days:
            uncertain.add("星期")
    if criteria.max_weekly_sessions is not None:
        if order.weekly_sessions and order.weekly_sessions > criteria.max_weekly_sessions:
            return _exclude(order, "订单每周次数超过当前最多次数")
        if order.weekly_sessions is None:
            uncertain.add("每周次数")
    if criteria.time_slots and not order.schedule_options:
        if order.time_slots and not set(order.time_slots).issubset(criteria.time_slots):
            return _exclude(order, "无法覆盖订单要求的上课时段")
        if not order.time_slots:
            uncertain.add("时间段")
    if criteria.min_hourly_rate is not None:
        maximum = order.hourly_rate_max if order.hourly_rate_max is not None else order.hourly_rate
        minimum = order.hourly_rate_min if order.hourly_rate_min is not None else order.hourly_rate
        if not order.price_incomplete and maximum is not None and maximum < criteria.min_hourly_rate:
            return _exclude(order, f"已知最高时薪 {maximum:g} 元低于最低要求 {criteria.min_hourly_rate:g} 元")
        if not price_known:
            uncertain.add("时薪")
        elif order.price_incomplete:
            uncertain.add("部分薪资")
        elif minimum < criteria.min_hourly_rate <= maximum:
            uncertain.add("时薪范围")

    if no_core_fields:
        order.uncertain_fields = sorted(uncertain)
        order.match_status = "无法解析"
        order.match_reason = "缺少可用的核心订单字段，请查看原文。"
        return order
    if order.split_uncertain:
        uncertain.add("订单边界")
    if order.extraction_source != "model":
        uncertain.add("模型解析")
    order.uncertain_fields = sorted(uncertain)
    if order.uncertain_fields:
        order.match_status = "可能匹配"
        reasons.append("待确认：" + "、".join(order.uncertain_fields))
    else:
        order.match_status = "明确匹配"
        reasons.append("已知字段满足当前筛选条件")
    if order.extraction_note:
        reasons.append(order.extraction_note)
    order.match_reason = "；".join(reasons)
    return order


def apply_criteria(orders: list[Order], criteria: Criteria) -> list[Order]:
    return [result for order in orders if (result := match_order(order, criteria)) is not None]


def match_order(order: Order, criteria: Criteria) -> Order | None:
    result = evaluate_order(order, criteria)
    return None if result.match_status == "已排除" else result
