"""地址字段边界回归：保留学员信息，并保证地点可用于单行查询。"""
from pathlib import Path
from unittest.mock import patch

import pytest

from tutor_screen.local_parser import parse_local
from tutor_screen.locations import location_spans, without_locations
from tutor_screen.maps import clean_query
from tutor_screen.matching import evaluate_order
from tutor_screen.models import Criteria, ExtractedOrder, Order
from tutor_screen.pipeline import process_batch
from tutor_screen.privacy import mask_for_model
from tutor_screen.text_fields import read_fields


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("label", ["地点:", "地址★", "【辅导地点】", "地址"])
def test_address_stops_at_line_end_even_before_unknown_field(label, newline):
    address = "温州龙湾滨海合成家园"
    raw = f"{label}  {address}　{newline}对象:初一学生，辅导数学/科学/英语{newline}时间:周六"
    span, = location_spans(raw)
    assert span.value == parse_local(raw).address == address
    assert raw[span.start:span.end] == address
    assert "对象" not in span.source
    for masked in (without_locations(raw), mask_for_model(raw)):
        assert address not in masked
        assert "对象:初一学生，辅导数学/科学/英语" in masked
        assert len(masked) == len(raw)
    assert clean_query(span.value) == address


@pytest.mark.parametrize("label", ["地址：", "【地址】", "地点"])
def test_address_stops_before_same_line_student_field(label):
    raw = f"{label}龙湾区合成锦园 对象:初一学生，辅导数学 时间:周六"
    assert parse_local(raw).address == "龙湾区合成锦园"
    assert "对象:初一学生" in mask_for_model(raw)
    student, = [field for field in read_fields(raw) if field.category == "student"]
    assert student.value == "初一学生，辅导数学"


@pytest.mark.parametrize("label", ["地址：", "【地址】", "【地址】："])
def test_empty_address_does_not_consume_following_line(label):
    raw = label + "\n未知标题：初一学生住龙湾区\n时间：周六"
    assert parse_local(raw).address is None
    assert "未知标题：初一学生住龙湾区" in without_locations(raw)


def test_other_fields_can_still_span_lines():
    raw = "学员情况：初一学生\n基础薄弱，需要巩固\n地址：测试小区\n时间：周六"
    student, = [field for field in read_fields(raw) if field.category == "student"]
    assert student.value == "初一学生\n基础薄弱，需要巩固"


@pytest.mark.parametrize("model_fails", [False, True])
def test_complete_user_batch_has_clean_addresses(model_fails):
    raw = (Path(__file__).parent / "fixtures/sixteen_orders_with_decoration.txt").read_text(encoding="utf-8")
    reply = RuntimeError("offline") if model_fails else lambda *_: ExtractedOrder()
    with patch("tutor_screen.pipeline.extract_order", side_effect=reply):
        orders = process_batch(raw, "synthetic", "https://example.test", "synthetic")
    assert len(orders) == 16
    by_number = {order.order_number: order for order in orders}
    assert by_number["wz5602"].address == "温州龙湾滨海合成家园"
    assert by_number["温州435397"].address == "龙湾区合成锦园"
    assert by_number["wz5624"].address == "龙湾或瓯北"
    assert by_number["温州线上435444"].mode == "线上"
    assert all(order.address and clean_query(order.address) == order.address for order in orders)


def test_existing_polluted_order_is_corrected_during_filtering():
    raw = "wz5602\n地点:温州龙湾滨海合成家园\n对象:初一学生，辅导数学\n时间:周六"
    old = Order(order_id="old", raw_text=raw, address="温州龙湾滨海合成家园\n对象:初一学生，辅导数学")
    result = evaluate_order(old, Criteria())
    assert result.address == "温州龙湾滨海合成家园"
    assert "对象" not in result.field_sources["address"]
    assert "对象" in old.address
