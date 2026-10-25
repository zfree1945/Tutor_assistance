"""Independent synthetic expectations for QA-01..10; no real provider calls."""
import csv
import io
from unittest.mock import patch

import pytest

from tutor_screen.export import orders_csv
from tutor_screen.local_parser import parse_local
from tutor_screen.matching import evaluate_order
from tutor_screen.models import Criteria, ExtractedOrder, Order
from tutor_screen.pipeline import process_batch


def order(schedule):
    raw = "订单号 QA100\n学员：小学\n科目：数学\n费用：150/h\n方式：线上\n时间：" + schedule
    return Order(**parse_local(raw).model_dump(), order_id="ORD-0001", raw_text=raw, extraction_source="model")


@pytest.mark.parametrize("separator", ["但", "但是", "，", "；", "。", "\n"])
def test_qa01_negative_scope(separator):
    value = order("周六不行" + separator + "周日可以")
    assert value.days == [7]
    assert value.excluded_days == [6]
    assert evaluate_order(value, Criteria(days=[6])).match_status == "已排除"
    assert evaluate_order(value, Criteria(days=[7])).match_status != "已排除"


@pytest.mark.parametrize("phrase", ["周六、周日均可，任选一天，每周一次", "周六周日都可以，任选一天，每周1次"])
@pytest.mark.parametrize("day", [6, 7])
def test_qa02_choose_one_day(phrase, day):
    value = order(phrase)
    assert value.weekly_sessions == 1
    assert evaluate_order(value, Criteria(days=[day])).match_status != "已排除"


@pytest.mark.parametrize("phrase", [
    "周六周日09:00-17:00，每天两节，每节2小时，中间休息",
    "周六周日09:00-17:00，每天2节，每节课2小时，中间休息",
])
def test_qa03_window_is_not_a_lesson(phrase):
    value = order(phrase)
    assert value.lesson_hours == 2
    assert value.weekly_sessions in (None, 4)
    assert len(value.schedule_windows) == 1


@pytest.mark.parametrize("phrase,expected", [
    ("周六09:00-11:00，周日14:00-16:00", [([6],540,660),([7],840,960)]),
    ("周日14:00-16:00；周六09:00-11:00", [([7],840,960),([6],540,660)]),
    ("周六09:00-11:00，14:00-16:00", [([6],540,660),([6],840,960)]),
    ("周日12:00-14:00", [([7],720,840)]),
    ("周日22:00-24:00", [([7],1320,1440)]),
])
def test_qa04_05_grouped_clocks(phrase, expected):
    value = order(phrase)
    assert [(w.days,w.start_minute,w.end_minute) for w in value.schedule_windows] == expected
    assert value.lesson_hours == 2


@pytest.mark.parametrize("phrase", ["周日23:00-01:00", "周日14:80-16:00", "周日1点到3点", "周日14:00-13:00", "周日09:00-11:00，14:90-16:00"])
def test_qa05_invalid_or_ambiguous_is_not_definite(phrase):
    value = order(phrase)
    assert value.lesson_hours is None
    assert evaluate_order(value, Criteria()).match_status == "可能匹配"
    assert value.raw_text.endswith(phrase)


@pytest.mark.parametrize("phrase", [
    "周日09:00-11:00，每周最多2次", "周日09:00-11:00，每周至少2次",
    "周日09:00-11:00，每周1-2次", "周日09:00-11:00，每周一次或两次",
    "单周周日09:00-11:00", "双周周日09:00-11:00",
    "周六09:00-11:00，周日09:00-11:00，每周一次",
    "每周一次，周六09:00-11:00，周日09:00-11:00",
])
def test_qa06_no_invented_exact_frequency(phrase):
    assert order(phrase).weekly_sessions is None


@pytest.mark.parametrize("price,low,high", [("180/2h",90,90),("150元一个小时，130一个小时",130,150),("200/2h，300/2h",100,150),("100/h，面议",100,100)])
def test_qa07_rates_and_monotonic_filter(price, low, high):
    value = order("周日晚上 每周1次 每次2小时")
    value.raw_text = value.raw_text.replace("150/h", price)
    evaluated = evaluate_order(value, Criteria())
    assert (evaluated.hourly_rate_min or evaluated.hourly_rate) == low
    assert (evaluated.hourly_rate_max or evaluated.hourly_rate) == high
    decisions = [evaluate_order(value, Criteria(min_hourly_rate=rate)).match_status != "已排除" for rate in [0,90,100,130,150,200]]
    assert decisions == sorted(decisions, reverse=True)


@pytest.mark.parametrize("separator", ["", "\n", "\n\n"])
def test_qa09_duplicate_ids_keep_both_orders_and_csv(separator):
    first = "订单号 QA100 小学数学 150/h 线上 周六晚上 每周1次"
    second = "订单号 QA100 初中英语 180/h 线上 周日上午 每周1次"
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw,*_: parse_local(raw)):
        values = process_batch(separator.join([first,second]), "SENTINEL", "https://example.test", "mock")
    assert [v.raw_text for v in values] == [first,second]
    assert len({v.order_id for v in values}) == 2
    rows = list(csv.DictReader(io.StringIO(orders_csv(values).decode("utf-8-sig"))))
    assert [r["raw_text"] for r in rows] == [first,second]


def test_qa10_valid_but_wrong_model_corrected_by_real_pipeline():
    raw = order("周日下午13:00-15:00，每周1次").raw_text
    wrong = ExtractedOrder(grade="高中", subjects=["物理"], hourly_rate=999, days=[1], weekly_sessions=6, time_slots=["上午"])
    with patch("tutor_screen.pipeline.extract_order", return_value=wrong):
        values = process_batch(raw, "SENTINEL", "https://example.test", "mock")
    result = evaluate_order(values[0], Criteria(grades=["小学"],subjects=["数学"],days=[7],min_hourly_rate=151))
    assert (result.grade,result.subjects,result.days,result.weekly_sessions) == ("小学",["数学"],[7],1)
    assert result.hourly_rate == 150
    assert result.match_status == "已排除"


@pytest.mark.parametrize("phrase", ["周六周日 每周2次", "周六09:00-11:00，周日09:00-11:00", "周六周日均要上课，每周2次"])
def test_fixed_days_still_require_both_days(phrase):
    value = order(phrase)
    assert evaluate_order(value, Criteria(days=[6])).match_status == "已排除"
    assert evaluate_order(value, Criteria(days=[6,7])).match_status != "已排除"


@pytest.mark.parametrize("phrase", ["周六不行但周日可以", "周日可以但周六不行", "周六不行，不过周日可以"])
def test_negative_scope_survives_real_model_pipeline(phrase):
    raw = order(phrase).raw_text
    with patch("tutor_screen.pipeline.extract_order", return_value=ExtractedOrder(days=[6,7])):
        result = process_batch(raw,"SENTINEL","https://example.test","mock")[0]
    result = evaluate_order(result,Criteria(days=[7]))
    assert result.days == [7] and result.excluded_days == [6]
    assert result.match_status != "已排除"


@pytest.mark.parametrize("phrase", ["周六09:00-11:00，周日09:00-11:00，每周2次", "每周2次，周日09:00-11:00，周六09:00-11:00"])
def test_consistent_fixed_frequency_stays_known(phrase):
    assert order(phrase).weekly_sessions == 2


@pytest.mark.parametrize("phrase", ["周六09:00-11:00，周日09:00-11:00，每周一次", "每周一次，周六09:00-11:00，周日09:00-11:00"])
def test_qa06_frequency_conflict_without_schedule_label_corrects_model(phrase):
    raw = "小学数学 150/h 线上 " + phrase
    with patch("tutor_screen.pipeline.extract_order",return_value=ExtractedOrder(weekly_sessions=1)):
        value = process_batch(raw,"SENTINEL","https://example.test","mock")[0]
    value = evaluate_order(value,Criteria(max_weekly_sessions=1))
    assert value.weekly_sessions is None
    assert value.match_status == "可能匹配"
    assert "每周次数" in value.uncertain_fields


@pytest.mark.parametrize("label", ["备注", "详情", "教师要求"])
def test_qa08_address_stops_before_teacher_requirements(label):
    raw = "订单号 QA102\n学员：小学\n科目：数学\n地址：测试市合成路999号\n" + label + "：女老师，英语专业\n费用：150/h"
    value = parse_local(raw)
    assert value.address == "测试市合成路999号"
    assert value.subjects == ["数学"]
    assert "女老师" not in value.field_sources["address"]
