"""IMP/SEG：订单身份、原文完整性、300条边界。"""
from unittest.mock import patch

import pytest

from tutor_screen.local_parser import parse_local
from tutor_screen.models import OrderBoundary
from tutor_screen.pipeline import TooManyOrders, process_batch
from tutor_screen.segmentation import resolve_model_boundaries, split_orders


@pytest.mark.parametrize("raw", ["订单号 A100\n\n学员：四年级\n\n科目：数学\n\n时薪：150\n时间：周六晚上", "学员：四年级\n\n科目：数学\n\n时薪：150"])
def test_blank_lines_inside_fields_do_not_create_orders(raw):
    pieces = split_orders(raw)
    assert len(pieces) == 1
    assert pieces[0].raw_text == raw


def test_mixed_numbers_duplicate_addresses_and_duplicate_numbers():
    raws = ["温州433255\n学员：四年级\n地址：相同小区", "S556313【初中全科】线上", "订单号 A100 小学数学 地址：相同小区", "订单号 A100 小学英语 线上"]
    text = "\n".join(raws)
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        orders = process_batch(text, "test-key", "https://example.test", "test-model")
    assert [v.raw_text for v in orders] == raws
    assert len({v.order_id for v in orders}) == 4


@pytest.mark.parametrize("count", [1, 299, 300, 301])
def test_order_limit(count):
    raw = "\n".join(f"订单号 A{i:03d} 小学数学 150/h 线上 周六晚上" for i in range(count))
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda text, *_: parse_local(text)) as extract:
        if count > 300:
            with pytest.raises(TooManyOrders):
                process_batch(raw, "test-key", "https://example.test", "test-model")
            extract.assert_not_called()
        else:
            orders = process_batch(raw, "test-key", "https://example.test", "test-model")
            assert len(orders) == count
            assert len({v.order_id for v in orders}) == count
            assert "\n".join(v.raw_text for v in orders) == raw


@pytest.mark.parametrize("boundaries", [[(1, "学员：初一"), (1, "学员：高三")], [(1, "学员：高三"), (1, "学员：高三")], [(99, "学员：高三")], [(1, "不存在")]])
def test_invalid_boundaries_preserve_whole_segment(boundaries):
    raw = "学员：高三；科目：数学；学员：初一；科目：英语"
    with pytest.raises(ValueError):
        resolve_model_boundaries(raw, raw, [OrderBoundary(line=line, anchor=anchor) for line, anchor in boundaries])
    with patch("tutor_screen.pipeline.try_split", side_effect=ValueError("bad boundary")), patch("tutor_screen.pipeline.extract_order", side_effect=lambda text, *_: parse_local(text)):
        result = process_batch(raw, "test-key", "https://example.test", "test-model")
    assert len(result) == 1 and result[0].raw_text == raw and result[0].split_uncertain
