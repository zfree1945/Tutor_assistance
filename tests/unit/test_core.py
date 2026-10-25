from unittest.mock import patch
from pathlib import Path

from tutor_screen.export import orders_csv
from tutor_screen.local_parser import parse_local
from tutor_screen.matching import match_order
from tutor_screen.models import Criteria, Order
from tutor_screen.pipeline import process_batch
from tutor_screen.privacy import mask_for_model
from tutor_screen.segmentation import split_orders


def make_order(text: str, index: int = 1, source: str = "model") -> Order:
    return Order(**parse_local(text).model_dump(), order_id=f"ORD-{index:04d}", raw_text=text, extraction_source=source)


def test_segmentation_keeps_duplicate_addresses_and_online_orders():
    text = "订单号 A100 小学数学 地址：同一小区 150元/小时\n订单号 A101 初中英语 地址：同一小区 200元/小时\n订单号 A102 高中物理 线上 250元/小时"
    pieces = split_orders(text)
    assert len(pieces) == 3
    assert "同一小区" in pieces[0].raw_text and "同一小区" in pieces[1].raw_text
    assert "线上" in pieces[2].raw_text


def test_privacy_masks_contact_and_address_before_model():
    text = "小学数学 地址：虹桥路88号；电话 13800138000；微信 wx_test123 周一"
    masked = mask_for_model(text)
    assert len(masked) == len(text)
    assert "虹桥路88号" not in masked
    assert "13800138000" not in masked
    assert "wx_test123" not in masked


def test_matching_rate_schedule_and_ambiguous_subject():
    order = make_order("小学三年级 数学 300元/2小时 线下 地址：某小区 时间：周一晚上 每周2次")
    good = match_order(order, Criteria(grades=["小学"], subjects=["数学"], days=[1], max_weekly_sessions=2, time_slots=["晚上"], min_hourly_rate=140))
    assert good and good.hourly_rate == 150 and good.match_status == "明确匹配"
    assert match_order(order, Criteria(days=[2])) is None
    assert match_order(order, Criteria(min_hourly_rate=160)) is None
    ambiguous = make_order("初中数理化 面议 线上 周日晚上 每周1次")
    result = match_order(ambiguous, Criteria(subjects=["物理"], min_hourly_rate=100))
    assert result and result.match_status == "可能匹配"


def test_batch_continues_after_model_failure_and_csv_keeps_raw_text():
    text = "订单号 A100 小学数学 150元/小时 线上 周一晚上 每周1次\n订单号 A101 初中英语 200元/小时 线上 周二晚上 每周1次"
    with patch("tutor_screen.pipeline.extract_order", side_effect=ValueError("invalid JSON")):
        orders = process_batch(text, "dummy", "https://example.test/v1", "dummy")
    assert [o.order_id for o in orders] == ["ORD-0001", "ORD-0002"]
    assert all(o.extraction_source == "local" for o in orders)
    matched = [match_order(o, Criteria()) for o in orders]
    assert all(o.match_status == "可能匹配" for o in matched)
    exported = orders_csv(matched).decode("utf-8-sig")
    assert "raw_text" in exported and "订单号 A101" in exported


def test_streamlit_pages_render_and_import_moves_to_results():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(Path(__file__).resolve().parents[2] / "app.py").run()
    assert not app.exception
    app.radio[0].set_value("模型设置").run()
    assert not app.exception
    app.radio[0].set_value("筛选结果").run()
    assert not app.exception
    app.radio[0].set_value("订单导入").run()
    app.session_state["api_key"] = "test-key"
    app.text_area[0].set_value("订单号 A100 小学数学 150元/小时 线上 周一晚上 每周1次")
    with patch("tutor_screen.pipeline.extract_order", return_value=parse_local("小学数学 150元/小时 线上 周一晚上 每周1次")):
        app.button(key="apply_filters").click().run()
    assert not app.exception
    assert app.session_state["page"] == "筛选结果"
    assert len(app.session_state["orders"]) == 1
