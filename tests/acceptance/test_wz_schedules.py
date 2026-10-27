"""钟点、固定星期、分组窗口、已有日志结果的回归。"""
import csv
import io
import json
from unittest.mock import patch

import pytest

from tutor_screen.export import orders_csv
from tutor_screen.local_parser import parse_local
from tutor_screen.matching import evaluate_order
from tutor_screen.models import Criteria, Order
from tutor_screen.pipeline import process_batch
from tutor_screen.privacy import mask_for_model
from tutor_screen.segmentation import split_orders
from wz_schedule_samples import EXPECTED, ORDERS, RAW


def windows(value):
    return [(item.days, item.start_minute, item.end_minute) for item in value.schedule_windows]


@pytest.mark.parametrize("raw,expected", list(zip(ORDERS, EXPECTED)), ids=[value["number"] for value in EXPECTED])
def test_user_four_orders_have_precise_independent_expected_fields(raw, expected):
    value = parse_local(raw)
    assert value.order_number == expected["number"]
    assert value.address == expected["address"]
    assert value.days == expected["days"] and value.weekly_sessions == expected["count"]
    assert value.lesson_hours == expected["hours"]
    assert windows(value) == expected["windows"]
    assert value.teacher_gender == "女" and value.subjects
    if expected["hours"] is not None:
        assert not {"lesson_hours", "每次时长"}.intersection(value.uncertain_fields)
    else:
        assert value.hourly_rate is None and "时薪" in value.uncertain_fields
        assert "每次时长" in value.uncertain_fields and "每周次数" not in value.uncertain_fields


@pytest.mark.parametrize("phrase,start,end", [
    ("周日下午1点到3点", 780, 900),
    ("周日下午一点到三点", 780, 900),
    ("周日13:00-15:00", 780, 900),
    ("周日 13：00 至 15：00", 780, 900),
    ("周日中午12点到下午2点半", 720, 870),
    ("周日晚上6点半到8点半", 1110, 1230),
    ("周日早上九点到十一点半", 540, 690),
    ("周日早上9点到晚上5点", 540, 1020),
    ("周日11:00-13:00", 660, 780),
    ("周日下午2点15分到4点45分", 855, 1005),
    ("周日晚上6:30到8:30", 1110, 1230),
    ("周日下午1-3点", 780, 900),
    ("周日上午12点到下午1点", 720, 780),
])
def test_clock_variants_infer_duration_and_preserve_window(phrase, start, end):
    value = parse_local("小学英语 60/h 线下\n时间：" + phrase)
    assert windows(value) == [([7], start, end)]
    assert value.lesson_hours == (end - start) / 60
    assert value.weekly_sessions == 1


@pytest.mark.parametrize("phrase", [
    "周日1点到3点", "周日25:00-27:00", "周日13:80-15:00",
    "周日下午3点到1点", "周日23:00-01:00", "周日下午13点到25点",
    "周日下午1点到3点之间任选一小时", "周日下午1点到3点可协商",
])
def test_invalid_or_flexible_clock_does_not_invent_lesson_duration(phrase):
    value = parse_local("小学英语 60/h 线下\n时间：" + phrase)
    assert value.lesson_hours is None and "每次时长" in value.uncertain_fields
    assert value.weekly_sessions is None


@pytest.mark.parametrize("phrase", [
    "周六晚上或周日上午", "周六或者周日13:00-15:00", "两周一次，周日13:00-15:00",
    "隔周周日13:00-15:00", "周日13:00-15:00，一周一到两次",
    "周日13:00-15:00，周日18:00-20:00", "周六13:00-15:00，周日时间待定",
    "每三周周日13:00-15:00", "单周周日13:00-15:00", "每隔一周周日13:00-15:00",
    "周日13:00-15:00，每周至少两次", "周日13:00-15:00，每周一次或两次",
])
def test_weekly_inference_requires_complete_fixed_single_windows(phrase):
    value = parse_local("小学英语 60/h 线下\n时间：" + phrase)
    assert value.weekly_sessions is None


def test_existing_log_fields_are_corrected_without_mutation_or_model_calls():
    old = [Order(order_id=f"ORD-{i+1:04}", raw_text=raw, grade="小学", subjects=["英语"],
                 address=expectation["address"] + "\n详情:女老师", days=expectation["days"],
                 weekly_sessions=expectation["count"] if i != 3 else None,
                 lesson_hours=None, uncertain_fields=["lesson_hours", "每次时长", "每周次数"],
                 extraction_source="model") for i, (raw, expectation) in enumerate(zip(ORDERS, EXPECTED))]
    before = [value.model_dump() for value in old]
    with patch("tutor_screen.llm_client._client") as client:
        evaluated = [evaluate_order(value, Criteria()) for value in old]
        client.assert_not_called()
    assert [value.model_dump() for value in old] == before
    for value, expectation in zip(evaluated, EXPECTED):
        assert value.address == expectation["address"] and value.lesson_hours == expectation["hours"]
        assert value.weekly_sessions == expectation["count"] and windows(value) == expectation["windows"]
    assert evaluated[0].match_status == "明确匹配"
    assert evaluated[3].match_status == "可能匹配"
    assert evaluate_order(old[3], Criteria(max_weekly_sessions=5)).match_status == "已排除"


def test_detail_does_not_extend_address_or_get_masked_as_address():
    for raw, expectation in zip(ORDERS, EXPECTED):
        value = parse_local(raw)
        assert "详情" not in value.address and "女老师" not in value.field_sources["address"]
        masked = mask_for_model(raw)
        assert expectation["address"] not in masked and "女老师" in masked
        assert len(raw) == len(masked)


@pytest.mark.parametrize("separator", ["\n\n", "\n", ""])
def test_short_wz_ids_are_distinct_boundaries_even_without_blank_lines(separator):
    segments = split_orders(separator.join(ORDERS))
    assert len(segments) == 4 and not any(segment.uncertain for segment in segments)
    assert [parse_local(segment.raw_text).order_number for segment in segments] == [e["number"] for e in EXPECTED]


def test_csv_preserves_window_groups_and_frequency():
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        batch = process_batch(RAW, "dummy-key", "https://example.test", "test")
    rows = list(csv.DictReader(io.StringIO(orders_csv(batch).decode("utf-8-sig"))))
    assert len(rows) == 4
    saved = json.loads(rows[3]["schedule_windows"])
    assert [(v["days"], v["start_minute"], v["end_minute"]) for v in saved] == EXPECTED[3]["windows"]
    assert rows[3]["weekly_sessions"] == "6" and rows[3]["lesson_hours"] == ""


@pytest.mark.parametrize("phrase", ["周日13:00-15:00，一次3小时", "周日13:00-15:00，一次小时"])
def test_conflicting_or_incomplete_explicit_duration_is_not_overwritten_by_window(phrase):
    value = parse_local("小学英语 60/h\n时间：" + phrase)
    assert value.lesson_hours is None and "每次时长" in value.uncertain_fields
