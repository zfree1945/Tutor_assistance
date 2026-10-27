"""模型故障时保守提取，所有降级订单仍须复核。"""

import re

from .models import ExtractedOrder, ScheduleOption
from .segmentation import find_order_markers
from .pricing import lesson_duration, normalize_price
from .text_fields import read_fields
from .locations import location_spans, without_locations
from .schedules import CLAUSE_SEPARATOR, FLEXIBLE, MULTIPLE_LESSONS, clock_windows, inferred_weekly_sessions, read_time_details
from .address_rules import ONLINE_ADDRESS, valid_address as _valid_local_address, validate_model_address
from .address_parts import split_address


SUBJECT_ALIASES = {
    "语文": ["语文", "语"], "数学": ["数学", "数学课"], "英语": ["英语", "英文"],
    "日语": ["日语"], "物理": ["物理"], "化学": ["化学"], "生物": ["生物"],
    "科学": ["科学"], "技术": ["技术"], "历史": ["历史"], "政治": ["政治", "道法"],
    "地理": ["地理"],
    "作业辅导": ["作业辅导"],
}
LOCAL_FIELDS_REVISION = "2026-10-26-model-address-extraction"
AMBIGUOUS_SUBJECTS = ["数理化", "全科", "理科", "文科"]
DAYS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
COUNTS = {**DAYS, "两": 2, "七": 7, "八": 8, "九": 9, "十": 10}
FREQUENCY = re.compile(r"(?:每(?:一)?周|一周|1周|每星期|一星期|每礼拜|一礼拜)\s*([\d一二两三四五六七八九十]+)(?:\s*[-~～至到]\s*([\d一二两三四五六七八九十]+))?\s*次(?:课)?")
PERIOD_FREQUENCY = re.compile(r"(?:每\s*)?(?:[\d一二两三四五六七八九十]+)\s*(?:周|星期|礼拜)\s*[\d一二两三四五六七八九十]+\s*次(?:课)?")
DAY = re.compile(r"(?:星期|礼拜|周)\s*([1-7一二三四五六日天])(?!\s*(?:次|节|课))")
DAY_RANGE = re.compile(r"(?:星期|礼拜|周)\s*([1-7一二三四五六日天])\s*(?:到|至|[-~～—])\s*(?:(?:星期|礼拜|周)\s*)?([1-7一二三四五六日天])")
FIELD_UNCERTAINTY = {
    "grade": {"年级", "grade"}, "subjects": {"科目", "subjects"},
    "mode": {"上课方式", "mode"}, "teacher_gender": {"教师性别要求", "teacher_gender"},
    "days": {"星期", "days", "day_options"}, "weekly_sessions": {"每周次数", "weekly_sessions"},
    "time_slots": {"时间段", "time_slots"}, "lesson_hours": {"每次时长", "lesson_hours"},
}
ADDRESS_UNCERTAINTY = {"address", "地址待确认", "地址范围", "上课地点"}


def _day_number(token: str) -> int:
    return int(token) if token.isdigit() else DAYS[token]


def _read_days(text: str) -> list[int]:
    days = {_day_number(match.group(1)) for match in DAY.finditer(text)}
    for match in DAY_RANGE.finditer(text):
        start, end = _day_number(match.group(1)), _day_number(match.group(2))
        days.update(range(start, end + 1) if start <= end else [*range(start, 8), *range(1, end + 1)])
    return sorted(days)


def _read_grade(text: str) -> tuple[str | None, str]:
    match = re.search(r"小学|初中|高中|小[一二三四五六1-6]|初[一二三1-3]|高[一二三1-3]", text)
    if match:
        value = match.group()
        return ("小学" if value.startswith("小") else "初中" if value.startswith("初") else "高中"), value
    match = re.search(r"(?<![\d一二三四五六七八九十])([1-9一二三四五六七八九])\s*年级", text)
    if match:
        token = match.group(1)
        number = int(token) if token.isdigit() else COUNTS[token]
        return ("小学" if number <= 6 else "初中"), match.group()
    return None, ""


def parse_local(text: str) -> ExtractedOrder:
    data = ExtractedOrder()
    sources: dict[str, str] = {}
    uncertain: list[str] = []
    fields = read_fields(text)
    def field_text(*categories: str) -> str:
        return "\n".join(field.value for field in fields if field.category in categories and field.value)
    markers = find_order_markers(text)
    if markers:
        data.order_number = markers[0].number
        sources["order_number"] = text[markers[0].start:markers[0].end]
    # 明确年级字段优先，不能被“学员情况：学习初中内容”等描述覆盖。
    data.grade, grade_source = _read_grade(field_text("grade"))
    if data.grade is None:
        data.grade, grade_source = _read_grade(field_text("student", "subject") or text)
    if data.grade:
        sources["grade"] = grade_source
    else:
        uncertain.append("年级")
    exact = []
    requirement_match = re.search(r"(?:老师|教师|教员)?要求\s*[:：]?", text)
    subject_context = text[:requirement_match.start()] if requirement_match else text
    subject_text = field_text("subject") or without_locations(subject_context)
    for canonical, aliases in SUBJECT_ALIASES.items():
        if any(a in subject_text for a in aliases if len(a) > 1):
            exact.append(canonical)
    ambiguous = [s for s in AMBIGUOUS_SUBJECTS if s in subject_text]
    data.subjects = exact + ambiguous
    if data.subjects:
        sources["subjects"] = "、".join(data.subjects)
    else:
        uncertain.append("科目")
    if ambiguous:
        uncertain.append("科目")
    uncertain.append("时薪")
    # 一次性的试课方式不能覆盖常规上课地点；没有线下地址时仍保留
    # 原有保守判断，避免把纯线上订单错误放到地图上。
    candidates = location_spans(text)
    addresses = [span for span in candidates if _valid_local_address(span.value)]
    mode_text = text
    if addresses:
        mode_text = re.sub(r"(?:第[一1]节课?|首次|第一堂课?)\s*(?:线上|网课|远程)\s*(?:试课|试听)", "", mode_text)
        mode_text = re.sub(r"(?:试课|试听)\s*(?:采用|使用|安排为|安排)?\s*(?:线上|网课|远程)", "", mode_text)
    mode_text = re.sub(r"(?:不(?:支持|接受|接|做|要)?|不能)\s*(?:线上|网课|远程|线下|上门|面授)", "", mode_text)
    online = bool(re.search(r"线上|网课|远程", mode_text))
    offline = bool(re.search(r"线下|上门|面授", mode_text))
    if addresses:
        data.address = addresses[0].value
        sources["address"] = addresses[0].source
        if split_address(data.address).relative:
            uncertain.append("地址范围")
    declared_addresses = [field.value for field in fields if field.category == "address"]
    if not declared_addresses:
        declared_addresses = re.findall(r"(?m)^[ \t]*(?:地址|地点)([^\r\n]*)", text)
    address_supplied = bool(candidates or declared_addresses or re.search(r"家住|住在|位于|上课在", text))
    online_only_address = bool(declared_addresses) and all(
        ONLINE_ADDRESS.fullmatch(re.sub(r"[ \t\u3000]+", "", value)) for value in declared_addresses)
    rejected = any(not _valid_local_address(span.value)
                   and not ONLINE_ADDRESS.fullmatch(re.sub(r"[ \t\u3000]+", "", span.value)) for span in candidates)
    if rejected or address_supplied and not addresses and not online_only_address:
        uncertain.append("地址待确认")
    data.mode = "线上/线下均可" if online and offline else "线上" if online else "线下" if offline or data.address else "未知"
    if data.mode == "未知":
        uncertain.append("上课方式")
    else:
        sources["mode"] = "原文包含线上及线下/上门安排" if online and offline else "线上/线下/地址线索"
    schedule = field_text("schedule") or text
    if field_text("schedule") or re.search(r"时间(?:可)?(?:协调|协商)|时间待定|时段不限", schedule):
        sources["schedule"] = schedule
    frequencies = list(FREQUENCY.finditer(schedule))
    # 删除频率表达式后识别星期，避免把“一周一次”内部的“周一”当作周一。
    days_text = FREQUENCY.sub(lambda match: " " * len(match.group()), schedule)
    days_text = PERIOD_FREQUENCY.sub(lambda match: " " * len(match.group()), days_text)
    data.days = _read_days(days_text)
    options = [_read_days(part) for part in re.split(r"或者|或", days_text)]
    if len(options) > 1 and all(options):
        data.day_options = options
    if data.days:
        sources["days"] = schedule
    else:
        uncertain.append("星期")
    counts = {int(match.group(1)) if match.group(1).isdigit() and len(match.group(1)) <= 9 else COUNTS.get(match.group(1)) for match in frequencies if not match.group(2)}
    ambiguous_frequency = re.search(r"次(?:课)?\s*(?:或者|或)\s*[\d一二两三四五六七八九十]+\s*次", schedule)
    if len(counts) == 1 and next(iter(counts)) and not ambiguous_frequency and not any(match.group(2) for match in frequencies):
        data.weekly_sessions = next(iter(counts))
        sources["weekly_sessions"] = "；".join(match.group() for match in frequencies)
    else:
        uncertain.append("每周次数")
    data.schedule_windows, unclear_window = clock_windows(days_text, _read_days)
    if data.schedule_windows:
        sources["schedule_windows"] = "；".join(window.source for window in data.schedule_windows)
    fixed_count = inferred_weekly_sessions(days_text, data.days, data.schedule_windows, unclear_window)
    if data.weekly_sessions and fixed_count and data.weekly_sessions != fixed_count:
        data.weekly_sessions = None
        uncertain.extend(["每周次数", "上课安排冲突"])
    if not frequencies and not PERIOD_FREQUENCY.search(schedule):
        inferred = inferred_weekly_sessions(days_text, data.days, data.schedule_windows, unclear_window)
        if inferred:
            data.weekly_sessions = inferred
            sources["weekly_sessions"] = f"固定上课日每日一个时间窗口，共{inferred}次：{schedule}"
            uncertain = [field for field in uncertain if field != "每周次数"]
    data.lesson_hours = lesson_duration(schedule)
    explicit_duration = re.search(r"每次(?:课)?|一次(?:课)?|每节(?:课)?|每课时", days_text)
    if data.schedule_windows or unclear_window:
        sources["lesson_hours"] = schedule
        durations = {(window.end_minute - window.start_minute) / 60 for window in data.schedule_windows}
        fixed_length = next(iter(durations)) if len(durations) == 1 and not unclear_window and not FLEXIBLE.search(schedule) and not MULTIPLE_LESSONS.search(schedule) else None
        if not explicit_duration:
            data.lesson_hours = fixed_length
        elif fixed_length and data.lesson_hours and fixed_length != data.lesson_hours:
            data.lesson_hours = None
        if data.lesson_hours is None:
            uncertain.append("每次时长")
    if explicit_duration:
        sources["lesson_hours"] = schedule
        if data.lesson_hours is None:
            uncertain.append("每次时长")
    data.time_slots, data.excluded_times, unclear_clock = read_time_details(schedule)
    if data.excluded_times:
        sources["excluded_times"] = "；".join(data.excluded_times)
        uncertain.append("排除时段")
        for clause in data.excluded_times:
            if not re.search(r"上午|下午|晚上|早上|晚\d|\d\s*(?:点|[:：]\d)", clause):
                data.excluded_days.extend(_read_days(clause))
        data.excluded_days = sorted(set(data.excluded_days))
        positive_schedule = "，".join(clause for clause in re.split(CLAUSE_SEPARATOR, days_text) if clause.strip() not in data.excluded_times)
        positive_days = _read_days(positive_schedule)
        data.days = positive_days or sorted(set(data.days) - set(data.excluded_days))
        if data.excluded_days:
            sources["excluded_days"] = "；".join(data.excluded_times)
        if not data.days and "星期" not in uncertain:
            uncertain.append("星期")
        days_text = positive_schedule if positive_days else days_text
        data.day_options = []
    if unclear_clock:
        uncertain.append("钟点时间")
    parts = re.split(r"或者|或", days_text)
    if len(parts) > 1 and all(_read_days(part) for part in parts):
        data.schedule_options = [ScheduleOption(days=_read_days(part), time_slots=read_time_details(part)[0]) for part in parts]
    elif re.search(r"任选\s*(?:一|1)\s*天", days_text) and len(data.days) > 1 and not data.schedule_windows:
        data.day_options = [[day] for day in data.days]
        data.schedule_options = [ScheduleOption(days=[day], time_slots=data.time_slots) for day in data.days]
    if data.time_slots:
        sources["time_slots"] = "、".join(data.time_slots)
    else:
        uncertain.append("时间段")
    teacher_field = bool(field_text("teacher") or requirement_match)
    teacher = field_text("teacher") or (text[requirement_match.end():] if requirement_match else text)
    male = bool(re.search(r"限男|男(?:性|在读|专职|大学生|教师|教员|老师|家教)", teacher) or teacher_field and re.search(r"男[学]?生|^\s*男(?:[，,；;\s]|$)", teacher))
    female = bool(re.search(r"限女|女(?:性|在读|专职|大学生|教师|教员|老师|家教)", teacher) or teacher_field and re.search(r"女[学]?生|^\s*女(?:[，,；;\s]|$)", teacher))
    reject_male = bool(re.search(r"(?:不要|不接受|不考虑|谢绝|拒绝)\s*男", teacher))
    reject_female = bool(re.search(r"(?:不要|不接受|不考虑|谢绝|拒绝)\s*女", teacher))
    if re.search(r"男女不限|性别不限|不限男女|不限性别|男女均可|男女都可", teacher) or male and female and re.search(r"或|均可|都可", teacher):
        data.teacher_gender = "不限"
    elif reject_male and not reject_female:
        data.teacher_gender = "女"
    elif reject_female and not reject_male:
        data.teacher_gender = "男"
    elif male and not female:
        data.teacher_gender = "男"
    elif female and not male:
        data.teacher_gender = "女"
    elif male and female or reject_male and reject_female:
        data.teacher_gender = "未知"
        uncertain.append("教师性别要求")
    else:
        data.teacher_gender = "不限"
    sources["teacher_gender"] = teacher if teacher_field else data.teacher_gender
    data.uncertain_fields = list(dict.fromkeys(uncertain))
    data.field_sources = sources
    normalize_price(data, text)
    return data


def normalize_local_fields(order: ExtractedOrder, text: str, local: ExtractedOrder | None = None, *, preserve_model_address: bool = False) -> None:
    """以原文中明确的本地证据修正模型结果和会话中旧批次的字段。"""
    local = local or parse_local(text)
    known = ["order_number", "grade", "subjects", "lesson_hours"]
    for field in known:
        if getattr(local, field):
            setattr(order, field, getattr(local, field))
            order.field_sources[field] = local.field_sources.get(field, "本地原文")
            if field != "subjects" or "科目" not in local.uncertain_fields:
                order.uncertain_fields = [value for value in order.uncertain_fields if value not in FIELD_UNCERTAINTY.get(field, set())]
    previous_address = order.address
    previously_pending = "地址待确认" in order.uncertain_fields
    model_address = validate_model_address(previous_address, text) if preserve_model_address else None
    if (model_address and local.address and _valid_local_address(local.address)
            and local.address not in model_address and model_address not in local.address):
        model_address = None
        if "地址存在多个候选" not in order.uncertain_fields:
            order.uncertain_fields.append("地址存在多个候选")
    order.address = model_address or (local.address if _valid_local_address(local.address) else None)
    order.field_sources.pop("address", None)
    order.uncertain_fields = [value for value in order.uncertain_fields if value not in ADDRESS_UNCERTAINTY]
    if order.address:
        order.field_sources["address"] = model_address or local.field_sources.get("address", "本地原文")
        if not model_address:
            order.uncertain_fields.extend(value for value in ("地址范围", "地址待确认") if value in local.uncertain_fields)
        if split_address(order.address).relative and "地址范围" not in order.uncertain_fields:
            order.uncertain_fields.append("地址范围")
        if model_address and order.mode == "未知" and local.mode == "未知":
            order.mode = "线下"
            order.field_sources["mode"] = "原文地址线索"
            order.uncertain_fields = [value for value in order.uncertain_fields if value not in FIELD_UNCERTAINTY["mode"]]
    elif previous_address or local.address or previously_pending or "地址待确认" in local.uncertain_fields:
        order.uncertain_fields.append("地址待确认")
    if "lesson_hours" in local.field_sources:
        order.lesson_hours = local.lesson_hours
        order.field_sources["lesson_hours"] = local.field_sources["lesson_hours"]
        order.uncertain_fields = [value for value in order.uncertain_fields if value not in FIELD_UNCERTAINTY["lesson_hours"]]
        if local.lesson_hours is None:
            order.uncertain_fields.append("每次时长")
    for field in ("mode", "teacher_gender"):
        value = getattr(local, field)
        if value != "未知":
            setattr(order, field, value)
            order.field_sources[field] = local.field_sources[field]
            order.uncertain_fields = [value for value in order.uncertain_fields if value not in FIELD_UNCERTAINTY[field]]
        elif field == "teacher_gender" and "教师性别要求" in local.uncertain_fields:
            order.teacher_gender = "未知"
            if "教师性别要求" not in order.uncertain_fields:
                order.uncertain_fields.append("教师性别要求")
    for field in ("days", "weekly_sessions", "time_slots"):
        if getattr(local, field) or field in local.field_sources or "schedule" in local.field_sources or local.excluded_times or field == "time_slots" and "钟点时间" in local.uncertain_fields:
            setattr(order, field, getattr(local, field))
            order.uncertain_fields = [value for value in order.uncertain_fields if value not in FIELD_UNCERTAINTY[field]]
            if getattr(local, field):
                order.field_sources[field] = local.field_sources[field]
            else:
                order.field_sources[field] = local.field_sources.get("schedule", "原文未指定")
                order.uncertain_fields.append({"days": "星期", "weekly_sessions": "每周次数", "time_slots": "时间段"}[field])
    if local.days or "schedule" in local.field_sources:
        order.day_options = local.day_options
        order.schedule_options = local.schedule_options
    order.schedule_windows = local.schedule_windows
    if local.schedule_windows:
        order.field_sources["schedule_windows"] = local.field_sources["schedule_windows"]
    else:
        order.field_sources.pop("schedule_windows", None)
    order.excluded_times = local.excluded_times
    order.excluded_days = local.excluded_days
    for field in ("排除时段", "钟点时间", "上课安排冲突"):
        if field in local.uncertain_fields and field not in order.uncertain_fields:
            order.uncertain_fields.append(field)
    if local.excluded_times:
        order.field_sources["excluded_times"] = local.field_sources["excluded_times"]
    if "schedule" in local.field_sources:
        order.field_sources["schedule"] = local.field_sources["schedule"]
    normalize_price(order, text)
