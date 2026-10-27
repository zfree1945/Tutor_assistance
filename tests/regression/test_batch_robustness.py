"""极端输入和批处理容错：应未知、可复核，不能令整批崩溃。"""
from unittest.mock import patch

import pytest

from tutor_screen.local_parser import parse_local
from tutor_screen.models import ExtractedOrder
from tutor_screen.pipeline import process_batch


@pytest.mark.parametrize("expression", ["每次" + "9" * 400 + "小时", "每周" + "9" * 4500 + "次"], ids=["overflow-duration", "overflow-frequency"])
def test_extreme_numeric_input_does_not_abort_next_order(expression):
    raw = f"订单号 A100 小学数学 150/h 线上\n时间：{expression}\n订单号 A101 初中英语 200/h 线上 周六晚上"
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        result = process_batch(raw, "key", "https://example.test", "model")
    assert len(result) == 2 and result[1].hourly_rate == 200
    assert result[0].lesson_hours is None and result[0].weekly_sessions is None


def test_unknown_second_fee_field_keeps_range_incomplete():
    raw = "订单号 A100 小学数学 线上 周六晚上\n课费：150/h\n待遇：200元一节（长度未定）"
    with patch("tutor_screen.pipeline.extract_order", return_value=ExtractedOrder(hourly_rate=150)):
        result = process_batch(raw, "key", "https://example.test", "model")
    assert result[0].price_incomplete
    assert result[0].hourly_rate is None and result[0].hourly_rate_min == 150


def test_local_parse_failure_preserves_raw_and_continues_next_order():
    from tutor_screen.llm_client import ModelReplyError
    from tutor_screen.matching import apply_criteria
    from tutor_screen.models import Criteria
    raw = "订单号 A100 损坏字段\n订单号 A101 初中英语 200/h 线上 周六晚上"
    normal = parse_local(raw.splitlines()[1])
    with patch("tutor_screen.pipeline.parse_local", side_effect=[ValueError("local failure with dummy-key"), normal]), patch("tutor_screen.pipeline.extract_order", side_effect=ModelReplyError("模拟模型无效回复")):
        result = process_batch(raw, "dummy-key", "https://example.test", "model")
    assert len(result) == 2 and result[0].raw_text == raw.splitlines()[0]
    assert result[0].extraction_source == "failed" and "dummy-key" not in result[0].extraction_note
    assert len(apply_criteria(result, Criteria())) == 2
