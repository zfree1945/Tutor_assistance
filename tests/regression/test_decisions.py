"""TIME/MATCH：独立条件表以及条件收紧的业务性质。"""
import itertools

import pytest

from tutor_screen.matching import apply_criteria, match_order
from tutor_screen.models import Criteria, Order
from helpers import order

BASE = "小学数学 150/h 线上 周六晚上 每周1次 要求：女老师"


@pytest.mark.parametrize("rate,retained", [(149.99, False), (150, True), (150.01, True)])
def test_pay_threshold(rate, retained):
    assert (match_order(order(BASE.replace("150/h", f"{rate}/h")), Criteria(min_hourly_rate=150)) is not None) == retained


@pytest.mark.parametrize("days,slots,retained", [([6], ["晚上"], True), ([7], ["上午"], True), ([6], ["上午"], False), ([7], ["晚上"], False), ([6, 7], ["晚上"], True), ([], ["上午"], True), ([6], [], True)])
def test_schedule_alternatives_preserve_complete_branches(days, slots, retained):
    raw = "小学数学 150/h 线上\n时间：每周1次，周六晚上或周日上午"
    result = match_order(order(raw), Criteria(days=days, time_slots=slots))
    assert (result is not None) == retained
    if retained:
        assert result.match_status == "明确匹配"


def test_independent_condition_truth_table():
    # 七个条件独立取“满足/明确不符”，任何一个明确不符均排除，共128个组合。
    for flags in itertools.product((True, False), repeat=7):
        gender, grade, subject, mode, day, frequency, rate = flags
        criteria = Criteria(teacher_gender="女" if gender else "男", grades=["小学"] if grade else ["高中"], subjects=["数学", "英语"] if subject else ["物理"], mode="线上" if mode else "线下", days=[6] if day else [1], max_weekly_sessions=1 if frequency else 1, min_hourly_rate=150 if rate else 151)
        raw = BASE if frequency else BASE.replace("每周1次", "每周2次")
        result = match_order(order(raw), criteria)
        assert (result is not None) == all(flags), flags
        if result:
            assert result.match_status == "明确匹配"


def test_known_hard_mismatch_survives_missing_core_fields():
    raw = "要求：女老师"
    value = Order(order_id="ORD-1", raw_text=raw, teacher_gender="女", extraction_source="model")
    assert match_order(value, Criteria(teacher_gender="男")) is None


def test_low_price_is_excluded_despite_missing_schedule():
    assert match_order(order("小学数学 100/h 线上"), Criteria(min_hourly_rate=150, time_slots=["晚上"])) is None


def test_unknown_price_keeps_known_range_without_false_maximum():
    result = match_order(order("小学数学 100/h 面议 线上 周六晚上"), Criteria(min_hourly_rate=200))
    assert result and result.match_status == "可能匹配" and "部分薪资" in result.uncertain_fields


def test_higher_threshold_never_adds_candidates_and_does_not_mutate_orders():
    values = [order(BASE.replace("150/h", price), order_id=f"ORD-{i}") for i, price in enumerate(["100/h", "140-160/h", "180/h", "150/h 面议"])]
    before = [v.model_dump() for v in values]
    previous = {v.order_id for v in values}
    for threshold in [0, 100, 140, 150, 160, 180, 200]:
        current = {v.order_id for v in apply_criteria(values, Criteria(min_hourly_rate=threshold))}
        assert current <= previous
        previous = current
    assert [v.model_dump() for v in values] == before
