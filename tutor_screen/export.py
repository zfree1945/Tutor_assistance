import csv
import io
import json

from .models import Order


COLUMNS = [
    "order_id", "order_number", "grade", "subjects", "hourly_rate", "hourly_rate_min", "hourly_rate_max", "price_incomplete", "price_text", "mode",
    "days", "day_options", "schedule_options", "schedule_windows", "weekly_sessions", "lesson_hours", "time_slots", "excluded_times", "excluded_days", "address", "teacher_gender", "match_status",
    "match_reason", "uncertain_fields", "field_sources", "raw_text",
]


def _safe(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")) else text


def orders_csv(orders: list[Order]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    for order in orders:
        data = order.model_dump()
        data["subjects"] = "、".join(order.subjects)
        data["days"] = "、".join(str(x) for x in order.days)
        data["time_slots"] = "、".join(order.time_slots)
        data["uncertain_fields"] = "、".join(order.uncertain_fields)
        data["field_sources"] = "；".join(f"{k}: {v}" for k, v in order.field_sources.items())
        data["schedule_windows"] = json.dumps(data.get("schedule_windows", []), ensure_ascii=False)
        writer.writerow({key: _safe(data.get(key)) for key in COLUMNS})
    return output.getvalue().encode("utf-8-sig")
