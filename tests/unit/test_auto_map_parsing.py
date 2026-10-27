"""保留七单原始粘连格式；试课与标题后的地点不能丢掉常规上课地址。"""
from unittest.mock import patch
from pathlib import Path

import pytest

from tutor_screen.local_parser import parse_local
from tutor_screen.models import Criteria, Order
from tutor_screen.matching import evaluate_order
from tutor_screen.pipeline import process_batch
from tutor_screen.privacy import mask_for_model
from test_segmentation import SAMPLE_ORDERS, EXPECTED_NUMBERS


RAW = SAMPLE_ORDERS.replace("220-240/2h\n温州433951", "220-2402h温州433951").replace("/2h", "2h").replace("/4h", "4h").replace("70/h", "70h")


@pytest.mark.parametrize("model_fails", [False, True])
def test_seven_glued_orders_keep_every_number_and_mappable_address(model_fails):
    with patch("tutor_screen.pipeline.extract_order", side_effect=RuntimeError("offline") if model_fails else lambda raw, *_: parse_local(raw)):
        orders = process_batch(RAW, "synthetic", "https://example.test", "offline")
    assert [o.order_number for o in orders] == EXPECTED_NUMBERS
    assert all(o.address for o in orders)
    assert all(o.mode == "线下" for o in orders)
    assert [(o.hourly_rate_min, o.hourly_rate_max) for o in orders] == [
        (110, 120), (60, 70), (175, 175), (70, 80), (80, 80), (100, 100), (90, 90),
    ]
    assert all(not o.price_incomplete and "时薪" not in o.uncertain_fields for o in orders)
    assert [o.order_number for o in orders if evaluate_order(o, Criteria(min_hourly_rate=150)).match_status != "已排除"] == ["温州433944"]
    assert orders[-1].address == "模拟街道合成湾3栋"
    assert "温州433951" not in orders[0].raw_text
    assert all(evaluate_order(o, Criteria()).match_status != "已排除" for o in orders)


def test_existing_session_corrects_trial_mode_without_another_model_call():
    raw = "初一数学 时间安排：周六下午\n教员要求：第一节课线上试课\n学员地址：模拟街道合成湾"
    value = Order(order_id="OLD", raw_text=raw, mode="线上", address="模拟街道合成湾")
    result = evaluate_order(value, Criteria())
    assert result.mode == "线下" and result.address == "模拟街道合成湾"


@pytest.mark.parametrize("text,mode", [
    ("初中数学 线上试课后继续线上辅导 地址：某小区", "线上"),
    ("初中英语 仅线上，不上门", "线上"),
    ("初中英语 线上或上门均可 地址：某小区", "线上/线下均可"),
    ("初中数学 第一节课线上试课", "线上"),
    ("初中数学 仅接线上试课 地址：某小区", "线上"),
])
def test_trial_fix_preserves_explicit_online_arrangements(text, mode):
    assert parse_local(text).mode == mode


def test_inline_location_is_masked_without_losing_student_and_subject():
    raw = "S556313【初中全科瓯海区】合成街道合成湾3栋 初3男生，全科比较薄弱，一周5次课，每次2小时"
    masked = mask_for_model(raw)
    assert len(masked) == len(raw)
    assert "合成湾3栋" not in masked
    assert "初3男生" in masked and "全科" in masked


def test_import_seven_orders_and_automatically_locate_without_start_buttons():
    from streamlit.testing.v1 import AppTest
    from tutor_screen import map_store
    from tutor_screen.maps import GeoPoint, GeoResult, MapCredentials

    map_store.save_settings(map_store.MapSettings(
        default_city="温州市",
        credentials=MapCredentials(js_key="synthetic-js", security_code="synthetic-security", web_key="synthetic-web"),
    ))
    app = AppTest.from_file(Path(__file__).parents[2] / "app.py", default_timeout=20)
    app.session_state["api_key"] = "synthetic-model"
    app.run()
    app.text_area(key="order_text").set_value(RAW).run()
    calls = []

    def locate(address, city):
        calls.append((address, city))
        return GeoResult(status="located", candidates=[GeoPoint(lng=120.7 + len(calls) * .001, lat=28.0, city=city)], resolver_revision=3)

    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)), patch("tutor_screen.maps.AmapClient.locate", side_effect=locate):
        app.button(key="apply_filters").click().run()
        # AppTest doesn't run real-time fragment timers. Advance the synthetic
        # queue clock explicitly to model the browser's next fragment tick.
        for _ in range(7):
            app.session_state["map_auto_next_at"] = 0.0
            app.run()
            assert not app.exception
        before = len(calls)
        app.run()
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.button(key="apply_filters").click().run()
        app.radio(key="nav_widget").set_value("筛选结果").run()
        assert len(calls) == before
    assert len(app.session_state["orders"]) == 7
    assert len(app.session_state["filtered"]) == 7
    assert [o.order_number for o in app.session_state["filtered"]] == EXPECTED_NUMBERS
    assert len(calls) == 7 and all(city == "温州市" for _, city in calls)
    assert len({address for address, _ in calls}) == 7
    assert all(raw not in str(calls) for raw in ["老师要求", "2404h", "教员要求"])
    assert not any(b.key in {"map_prepare_batch", "map_start_batch"} for b in app.button)
    assert any("本批已解析 7 单 · 当前保留 7 单 · 地图显示 7 单" in c.value for c in app.caption)
