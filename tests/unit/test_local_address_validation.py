"""本地拒绝污染地址，重算时清理历史字段，不依赖地图接口清洗。"""
from unittest.mock import patch

import pytest

from tutor_screen.local_parser import normalize_local_fields, parse_local
from tutor_screen.locations import LocationSpan
from tutor_screen.map_ui import prepare_batch
from tutor_screen.maps import MapAPIError, clean_query
from tutor_screen.matching import evaluate_order
from tutor_screen.models import Criteria, ExtractedOrder, Order


@pytest.mark.parametrize("candidate", [
    "测试小区\n对象:初一学生", "测试小区\r备注:数学", "测试小区\u2028初一学生",
    "测试小区 科目:数学", "测试小区 信息:初一学生", "测试小区 辅导数学",
    "测试小区 女大学生，有经验", "测试小区 13800138000", "测试小区 微信:private123",
    "测试小区 " + "甲" * 161,
])
def test_parser_rejects_polluted_candidates_before_mapping(candidate):
    span = LocationSpan(candidate, 0, len(candidate), candidate, True)
    with patch("tutor_screen.local_parser.location_spans", return_value=[span]):
        parsed = parse_local("初一数学 线下 周六 100/h")
    assert parsed.address is None
    assert "address" not in parsed.field_sources
    assert "地址待确认" in parsed.uncertain_fields


@pytest.mark.parametrize("mode", ["", "线上"])
@pytest.mark.parametrize("value", ["测试小区 初一学生，辅导数学", "测试小区 信息:四年级", "测试小区基础薄弱", "未知", ""])
def test_real_bad_address_fields_are_marked_even_when_span_reader_skips_them(value, mode):
    parsed = parse_local(f"初一数学 {mode}\n地址：{value}\n时间：周六")
    assert parsed.address is None
    assert "地址待确认" in parsed.uncertain_fields


@pytest.mark.parametrize("address", ["龙湾或瓯北", "温州龙湾滨海合成家园", "教师公寓3栋", "科学馆附近", "西湖小学旁边", "#瓯海区三垟街道金域中央"])
def test_valid_places_and_landmarks_are_kept(address):
    parsed = parse_local(f"初一数学\n地址：{address}\n时间：周六")
    assert parsed.address == address
    assert "地址待确认" not in parsed.uncertain_fields


@pytest.mark.parametrize("raw", ["初一数学 线下 周六", "初一数学\n地址：测试小区基础薄弱"])
def test_recalculating_legacy_order_clears_stale_address_source_and_map_eligibility(raw):
    old = Order(order_id="old", raw_text=raw, address="测试小区\n对象:初一学生", mode="线下",
                field_sources={"address": "旧污染来源"}, uncertain_fields=["地址范围"])
    result = evaluate_order(old, Criteria())
    assert result.address is None and "address" not in result.field_sources
    assert "地址待确认" in result.uncertain_fields and "地址范围" not in result.uncertain_fields
    assert evaluate_order(result, Criteria()).model_dump() == result.model_dump()
    with patch("tutor_screen.map_ui.lookup_geo") as lookup:
        assert prepare_batch([result])["pending"] == []
        lookup.assert_not_called()
    assert old.address == "测试小区\n对象:初一学生"


def test_valid_recomputed_address_replaces_pollution_and_clears_old_uncertainty():
    raw = "地点:温州龙湾滨海合成家园\n对象:初一学生，辅导数学"
    order = Order(order_id="old", raw_text=raw, address="温州龙湾滨海合成家园\n对象:初一学生",
                  uncertain_fields=["地址待确认", "地址范围", "科目"])
    normalize_local_fields(order, raw)
    first = order.model_dump()
    normalize_local_fields(order, raw)
    assert order.model_dump() == first
    assert order.address == "温州龙湾滨海合成家园"
    assert "对象" not in order.field_sources["address"]
    assert not {"地址待确认", "地址范围"}.intersection(order.uncertain_fields)


def test_normalizer_validates_supplied_local_data_too():
    order = ExtractedOrder(address="旧地址")
    normalize_local_fields(order, "初一数学", ExtractedOrder(address="测试小区\n教学描述"))
    assert order.address is None and "地址待确认" in order.uncertain_fields


def test_online_or_absent_address_does_not_add_unnecessary_confirmation():
    for raw in ("初一数学 线上\n地址：线上", "初一数学\n地址：线上辅导", "初一数学 线上", "初一数学"):
        assert "地址待确认" not in parse_local(raw).uncertain_fields


def test_map_single_line_guard_still_rejects_direct_polluted_inputs():
    with pytest.raises(MapAPIError, match="单行"):
        clean_query("测试小区\n对象:初一学生")
