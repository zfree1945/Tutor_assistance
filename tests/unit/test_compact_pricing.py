"""粘连报价的独立语义、筛选门槛和旧会话修正回归。"""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen.local_parser import parse_local
from tutor_screen.matching import evaluate_order
from tutor_screen.models import Criteria, ExtractedOrder, Order
from tutor_screen.pipeline import process_batch
from tutor_screen.pricing import lesson_duration, parse_price


SCREENSHOT_FORMATS = [
    "订单号 PAY101\n辅导科目：高三数学\n时间安排：两周一次，周六晚上，一次两小时\n老师课费：220-2402h",
    "订单号 PAY102\n辅导科目：初一数学英语\n时间安排：两周一次 周六下午四小时\n老师薪水：2404h（后续上的好的话70h）",
]


@pytest.mark.parametrize("quote,low,high", [
    ("1802h", 90, 90), ("2404h", 60, 60), ("3502H", 175, 175),
    ("220-2402h", 110, 120), ("220～2402h", 110, 120),
    ("220 至 2402小时", 110, 120), ("220-2402.5h", 88, 96),
    ("1802.5h", 72, 72), ("70h", 70, 70), ("75h", 75, 75),
    ("150h", 150, 150), ("150.5h", 150.5, 150.5), ("60-80h", 60, 80),
    ("2404h（后续上的好的话70h）", 60, 70),
    ("2002h、3002h", 100, 150),
])
def test_compact_quotes_have_expected_hourly_bounds(quote, low, high):
    price = parse_price("老师课费：" + quote)
    assert (price.lower, price.upper) == (low, high)
    assert not price.has_unknown
    assert price.hourly_rate == (low if low == high else None)


@pytest.mark.parametrize("hours", range(1, 10))
def test_default_single_digit_duration_covers_one_through_nine_hours(hours):
    # 无时长上下文的单价和区间，与明确分隔符的人工时薪标注一致。
    single = parse_price(f"老师课费：{180 * hours}{hours}h")
    interval = parse_price(f"老师课费：{110 * hours}-{120 * hours}{hours}h")
    assert (single.lower, single.upper) == (180, 180)
    assert (interval.lower, interval.upper) == (110, 120)
    assert "不超过9小时" in single.evidence


@pytest.mark.parametrize("context,quote,rate", [
    ("每次两小时", "1802h", 90),
    ("周六下午四小时", "2404h", 60),
    ("一次2.5小时", "1802.5h", 72),
    ("每次12小时", "24012h", 20),
    ("每次10小时", "18010h", 18),
])
def test_explicit_duration_has_priority_over_default_split(context, quote, rate):
    value = parse_price(f"时间安排：{context}\n老师课费：{quote}")
    assert value.hourly_rate == rate
    assert "按原文" in value.evidence


@pytest.mark.parametrize("text", [
    "时间：每次3小时\n老师课费：1802h",
    "时间：每次2小时或者每次3小时\n老师课费：1802h",
    "时间：周六下午四小时，每次2小时\n老师课费：2404h",
    "老师课费：240-2202h", "老师课费：-1802h", "老师课费：- 1802h",
    "老师课费：− 220-2402h",
    "老师课费：220--2402h", "老师课费：180/0h",
])
def test_conflicting_or_invalid_quotes_do_not_get_a_known_rate(text):
    price = parse_price(text)
    assert price.lower is None and price.upper is None


@pytest.mark.parametrize("text,rate", [
    ("时薪：125h", 125), ("每小时1802元", 1802),
    ("老师课费：180/12h", 15), ("老师课费：180/2h", 90),
])
def test_explicit_hourly_labels_and_separators_keep_their_meaning(text, rate):
    assert parse_price(text).hourly_rate == rate


@pytest.mark.parametrize("text", [
    "时间安排：每次12h", "时间安排：每周总计180h",
    "时间安排：周六下午14h", "每次12h", "累计180h",
    "学员情况：已学180h", "地址：测试路1802h楼", "已学180h", "周六12h",
])
def test_duration_and_other_fields_are_not_compact_salary(text):
    price = parse_price(text)
    assert price.lower is None and price.upper is None


def test_period_duration_is_available_to_the_local_order_parser():
    value = parse_local(SCREENSHOT_FORMATS[1])
    assert value.lesson_hours == 4
    assert lesson_duration("时间：周六下午四小时") == 4
    assert (value.hourly_rate_min, value.hourly_rate_max) == (60, 70)
    assert not value.price_incomplete
    assert "时薪" not in value.uncertain_fields


def test_student_score_cannot_borrow_price_unit_from_next_field_label():
    price = parse_price("辅导科目：科学70-75/100\n时间安排：周五周六\n老师薪水：140-1602h")
    assert (price.lower, price.upper) == (70, 80)
    assert price.raw_text == "140-1602h"


@pytest.mark.parametrize("model_fails", [False, True])
def test_import_corrects_wrong_model_or_local_fallback_and_filters_both_orders(model_fails):
    with patch("tutor_screen.pipeline.extract_order", side_effect=(
        RuntimeError("offline") if model_fails else lambda *_: ExtractedOrder(hourly_rate=999)
    )):
        orders = process_batch("\n\n".join(SCREENSHOT_FORMATS), "synthetic", "https://example.test", "offline")
    assert len(orders) == 2
    assert [(order.hourly_rate_min, order.hourly_rate_max) for order in orders] == [(110, 120), (60, 70)]
    for threshold, retained in [(0, 2), (60, 2), (70, 2), (71, 1), (110, 1), (120, 1), (121, 0), (150, 0)]:
        results = [evaluate_order(order, Criteria(min_hourly_rate=threshold)) for order in orders]
        assert sum(result.match_status != "已排除" for result in results) == retained
    crossing = evaluate_order(orders[0], Criteria(min_hourly_rate=115))
    assert crossing.match_status == "可能匹配" and "时薪范围" in crossing.uncertain_fields


def test_negotiable_quote_keeps_known_rate_and_uncertainty():
    raw = "小学数学 线上\n老师课费：2404h，后续面议"
    value = evaluate_order(Order(order_id="PAY", raw_text=raw), Criteria(min_hourly_rate=150))
    assert (value.hourly_rate_min, value.hourly_rate_max) == (60, 60)
    assert value.price_incomplete and "部分薪资" in value.uncertain_fields
    assert value.match_status == "可能匹配"


def test_results_page_repairs_old_batch_and_reapplies_hourly_threshold_without_model_calls():
    old = [Order(order_id=f"OLD-{index}", raw_text=raw, uncertain_fields=["时薪"])
           for index, raw in enumerate(SCREENSHOT_FORMATS)]
    app = AppTest.from_file(Path(__file__).resolve().parents[2] / "app.py")
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = old
    app.session_state["filtered"] = old
    app.session_state["_history_cleared"] = True
    app.session_state["_local_fields_revision"] = "2026-10-17-trial-and-inline-location"
    with patch("tutor_screen.llm_client._client") as client:
        app.run()
        assert not app.exception
        assert [(value.hourly_rate_min, value.hourly_rate_max) for value in app.session_state["filtered"]] == [(110, 120), (60, 70)]
        assert any("¥110–120/小时" in item.value for item in app.markdown)
        assert any("¥60–70/小时" in item.value for item in app.markdown)
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.number_input(key="filter_min_rate").set_value(150.0).run()
        app.button(key="apply_filters").click().run()
        assert not app.exception and app.session_state["filtered"] == []
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.number_input(key="filter_min_rate").set_value(115.0).run()
        app.button(key="apply_filters").click().run()
        assert not app.exception and len(app.session_state["filtered"]) == 1
        assert app.session_state["filtered"][0].hourly_rate_max == 120
    client.assert_not_called()
