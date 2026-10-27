"""只为独立的地址/地点行补分隔符缺失的识别与脱敏。"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tutor_screen.llm_client import extract_order
from tutor_screen.local_parser import parse_local
from tutor_screen.locations import location_spans, without_locations
from tutor_screen.privacy import mask_for_model
from tutor_screen.text_fields import read_fields


SAMPLES = [
    ("温州59434286\n地址瑞安市合成镇示例服务中心附近\n学员★高一 男孩\n科目★数学\n时间★一周1次，一次2个小时，周日早上8-10点，时间可协商\n要求★男大学生，认真负责\n报酬★100一个小时", "瑞安市合成镇示例服务中心附近"),
    ("温州435397\n高三男孩 辅导通用课程\n周一到周五晚上辅导\n一次两小时200\n男女不限，认真负责\n地点龙湾区合成锦园", "龙湾区合成锦园"),
]


@pytest.mark.parametrize("raw,address", SAMPLES)
def test_real_address_lines_are_parsed_and_masked(raw, address):
    parsed = parse_local(raw)
    assert parsed.address == address and parsed.mode == "线下"
    span, = location_spans(raw)
    assert span.value == raw[span.start:span.end] == address
    for masked in (without_locations(raw), mask_for_model(raw)):
        assert address not in masked and len(masked) == len(raw)
        assert [i for i, ch in enumerate(masked) if ch == "\n"] == [i for i, ch in enumerate(raw) if ch == "\n"]
        assert "认真负责" in masked


def test_indented_address_stops_at_line_end_and_preserves_offsets():
    raw = "初中数学\r\n\t地址龙湾区合成锦园  \r\n时间可协商\n老师要求认真负责"
    span, = location_spans(raw)
    assert span.value == raw[span.start:span.end] == "龙湾区合成锦园"
    assert "时间可协商\n老师要求认真负责" in without_locations(raw)


@pytest.mark.parametrize("address", ["瑞 安市合成镇示例服务中心附近", "瑞安市 马屿镇 镇政府附近", "龙 湾 区 和 家 锦 园", "示例组 团", "龙\t湾区合成锦园", "龙　湾区合成锦园"])
@pytest.mark.parametrize("label", ["地址", "地点", "地址：", "地址★"])
def test_spaces_inside_and_after_address_preserve_extraction_and_masking(address, label):
    raw = f"初中数学\n{label}{address}  \t\n时间：周末"
    span, = location_spans(raw)
    assert parse_local(raw).address == span.value == address
    assert raw[span.start:span.end].strip() == address
    for masked in (without_locations(raw), mask_for_model(raw)):
        assert address not in masked and len(masked) == len(raw)
        assert "时间：周末" in masked
        assert not masked[raw.index(address):raw.index(address) + len(address)].strip(" █\t　")


@pytest.mark.parametrize("line", ["地址周 日到市区", "地址基 础薄弱的市区学生", "地址未 知", "地点线 上"])
def test_spaces_do_not_bypass_non_location_checks(line):
    assert location_spans(line) == []


def test_fullwidth_trailing_space_is_trimmed_from_bare_address():
    assert parse_local("地址龙　湾区合成锦园　").address == "龙　湾区合成锦园"


@pytest.mark.parametrize("line", [
    "时间可协商", "老师要求女生", "要求女生，熟悉龙湾区教材", "请确认地址龙湾区合成锦园",
    "温州59434286地址瑞安市合成镇示例服务中心附近", "学员地址龙湾区合成锦园",
    "地址待定", "地点未知", "地址线上", "地点可协商", "地址基础薄弱",
    "地址没有地名标志", "地址龙1区", "地点周日到市区",
])
def test_narrative_nonstandalone_and_invalid_lines_are_not_locations(line):
    raw = "初中数学\n" + line
    assert location_spans(raw) == []
    assert parse_local(raw).address is None
    assert without_locations(raw) == raw


@pytest.mark.parametrize("label", ["地址：", "地址★", "地点:", "地点☆", "【地址】"])
def test_explicit_formats_are_unchanged_and_not_duplicated(label):
    raw = label + "瑞安市合成镇示例服务中心附近\n时间：周末"
    span, = location_spans(raw)
    assert span.value == parse_local(raw).address == "瑞安市合成镇示例服务中心附近"


def test_explicit_address_has_priority_and_all_occurrences_are_masked():
    raw = "地点龙湾区合成锦园\n地址：瑞安市合成镇示例服务中心附近\n科目：数学"
    assert parse_local(raw).address == "瑞安市合成镇示例服务中心附近"
    spans = location_spans(raw)
    assert len(spans) == 2
    assert all(span.value not in without_locations(raw) for span in spans)
    repeated = "地址龙湾区合成锦园\n地点龙湾区合成锦园"
    assert len(location_spans(repeated)) == 2
    assert "龙湾区合成锦园" not in without_locations(repeated)


def test_general_label_rules_are_unchanged():
    assert read_fields("时间可协商\n老师要求女生\n地址龙湾区合成锦园") == []


@pytest.mark.parametrize("raw,address", SAMPLES)
def test_actual_model_payload_preserves_bare_address_for_extraction(raw, address):
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = reply
        extract_order(raw, "synthetic", "https://example.test", "synthetic")
        sent = client.return_value.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
    assert address in sent and "认真负责" in sent
