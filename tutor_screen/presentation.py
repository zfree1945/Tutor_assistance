"""结果页使用业务名称；日志保留原始字段。"""
import re

FIELD_LABELS = {
    "grade": "年级", "subjects": "科目", "hourly_rate": "时薪", "hourly_rate_min": "时薪", "hourly_rate_max": "时薪",
    "price_text": "时薪", "price_incomplete": "部分薪资", "mode": "上课方式", "days": "星期", "day_options": "可选上课安排",
    "schedule_options": "可选上课安排", "weekly_sessions": "每周次数", "lesson_hours": "每次时长", "time_slots": "时间段",
    "address": "上课地点", "teacher_gender": "教师性别要求", "order_number": "订单编号", "excluded_times": "排除时段", "excluded_days": "排除星期",
    "field_sources": "识别依据", "模型解析": "识别结果需核对", "本地解析": "识别结果需核对",
    "地址范围": "具体上课地点", "钟点时间": "具体上课时间", "订单边界": "订单是否混在一起",
}


def confirmation_labels(fields: list[str]) -> list[str]:
    labels = []
    for field in fields:
        label = FIELD_LABELS.get(field, field)
        if re.fullmatch(r"[A-Za-z0-9_]+", label):
            label = "其他订单信息"
        if label not in labels:
            labels.append(label)
    return labels
