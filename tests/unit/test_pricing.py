from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest
from pathlib import Path

from tutor_screen.local_parser import parse_local
from tutor_screen.matching import match_order
from tutor_screen.models import Criteria, ExtractedOrder, Order
from tutor_screen.pipeline import process_batch
from tutor_screen.pricing import parse_price


@pytest.mark.parametrize("expression, expected", [
    ("180/2h", 90), ("180/2H", 90), ("180／2h", 90),
    ("240/4h", 60), ("350/2h", 175), ("300元/2小时", 150),
    ("180元2h", 90), ("180 2h", 90), ("180/小时", 180),
    ("70/h", 70), ("时薪：180元", 180), ("每小时150", 150),
    ("时薪180/2h", 90), ("180/两小时", 90),
])
def test_normalize_hourly_and_lesson_fees(expression, expected):
    price = parse_price(expression)
    assert price.hourly_rate == expected
    assert price.lower == price.upper == expected


@pytest.mark.parametrize("context", ["每次2小时", "每次两小时", "一次两小时"])
def test_missing_separator_uses_independent_lesson_duration(context):
    order = parse_local(f"初中全科 一周5次课，{context}，周一到周五晚上，1802h")
    assert order.hourly_rate == 90
    assert order.price_text == "1802h"
    assert "时薪" not in order.uncertain_fields
    assert "补全分隔符" in order.field_sources["hourly_rate"]


@pytest.mark.parametrize("text", ["每次3小时，1802h", "每次2小时或者每次3小时，1802h", "180/0h", "面议", "180/课时"])
def test_unconvertible_fee_is_not_guessed(text):
    price = parse_price(text)
    assert price.hourly_rate is None
    assert price.upper is None


def test_ranges_are_converted_without_taking_only_last_endpoint():
    price = parse_price("科学70-75/100 老师薪水：140-160/2h")
    assert price.hourly_rate is None
    assert (price.lower, price.upper) == (70, 80)
    order = Order(**parse_local("初二科学 线上 周六晚上 140-160/2h").model_dump(), order_id="ORD-1", raw_text="初二科学 线上 周六晚上 140-160/2h", extraction_source="model")
    assert match_order(order, Criteria(min_hourly_rate=150)) is None
    result = match_order(order, Criteria(min_hourly_rate=75))
    assert result and result.match_status == "可能匹配"


def test_conditional_future_price_is_not_treated_as_the_only_rate():
    price = parse_price("老师薪水：240/4h（后续上的好的话70/h）")
    assert price.hourly_rate is None
    assert (price.lower, price.upper) == (60, 70)
    assert "240/4h" in price.raw_text and "70/h" in price.raw_text


def test_local_price_overrides_wrong_model_price_before_filtering():
    text = "S556313【初中全科】初3男生，一周5次课，每次2小时，周一到周五晚上，1802h"
    wrong = ExtractedOrder(grade="初中", subjects=["全科"], hourly_rate=180, uncertain_fields=["时薪", "上课方式"])
    with patch("tutor_screen.pipeline.extract_order", return_value=wrong):
        orders = process_batch(text, "dummy", "https://example.test", "dummy")
    assert orders[0].hourly_rate == 90
    assert "时薪" not in orders[0].uncertain_fields
    assert match_order(orders[0], Criteria(teacher_gender="女", subjects=["物理", "化学"], min_hourly_rate=150)) is None


def test_applying_filters_repairs_previously_parsed_batch_without_api_calls():
    text = "S556313【初中全科】初3男生，一周5次课，每次2小时，周一到周五晚上，1802h"
    old_order = Order(order_id="ORD-0007", raw_text=text, grade="初中", subjects=["全科"], hourly_rate=None, uncertain_fields=["时薪"], extraction_source="local")
    app = AppTest.from_file(Path(__file__).resolve().parents[2] / "app.py")
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [old_order]
    app.session_state["filtered"] = [old_order]
    app.run()
    with patch("tutor_screen.llm_client._client") as client:
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.number_input(key="filter_min_rate").set_value(150.0).run()
        app.button(key="apply_filters").click().run()
    assert not app.exception
    assert app.session_state["filtered"] == []
    client.assert_not_called()
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.number_input(key="filter_min_rate").set_value(80.0).run()
    app.button(key="apply_filters").click().run()
    assert not app.exception
    assert app.session_state["filtered"][0].hourly_rate == 90
    app.radio(key="nav_widget").set_value("筛选结果").run()
    assert any("¥90/小时" in item.value for item in app.markdown)
