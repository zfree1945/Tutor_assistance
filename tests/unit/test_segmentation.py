"""粘连订单回归；保留用户样例的格式，地址使用模拟内容。"""

from unittest.mock import patch
from types import SimpleNamespace
import json

import pytest

from tutor_screen.local_parser import parse_local
from tutor_screen.llm_client import try_split
from tutor_screen.matching import match_order
from tutor_screen.models import Criteria, OrderBoundary
from tutor_screen.pipeline import TooManyOrders, process_batch
from tutor_screen.privacy import mask_for_model
from tutor_screen.segmentation import resolve_model_boundaries, split_orders


SAMPLE_ORDERS = """温州100922
【上课地址】：温州市某区某路模拟小区
【辅导科目】：高三数学
【学员情况】：女孩基础较薄弱
【时间安排】：两周一次，周六晚上，一次两小时
【老师要求】：男女不限，经验丰富，有耐心
【老师课费】：220-240/2h
温州100628
学员情况：初一
辅导科目：数学英语（基础一般 英语作文不会写）
时间安排：两周一次 周六下午四小时
教员要求：男女不限 认真负责（第一节课线上试课）
学员地址：某区某路
老师薪水：240/4h（后续上的好的话70/h）

温州100611
学员情况：初一
辅导科目：科学
时间安排：周六下午 两周一次
教员要求：专业老师
学员地址：模拟小区(某路)
老师薪水：350/2h

温州092904
学员情况：初二女
辅导科目：科学70-75/100
时间安排：周五周六
教员要求：医科大女老师 逻辑思维强的
学员地址：某区某路模拟店对面
老师薪水：140-160/2h

温州092884
学员情况：初一男
辅导科目：科学( 基础较好）
时间安排：周六十点后
教员要求：男老师
学员地址：某区模拟小区附近
老师薪水：160/2h

温州092701
上课地址：某区某路模拟小区710
年级科目：高二物化技 女（主物化）
时间安排：一周一次 周六晚上六点或者周日早上八点 一次两小时
老师要求：女老师 经验丰富
老师课费：大学生200/2h

S223080【初中全科某区】模拟街道南丽湾3栋 初3男生，全科比较薄弱需要全科补习 查漏补缺+作业辅导，一周5次课，每次2小时，周一到周五晚上，180/2h，要求教学经验丰富的 细心责任心较强的 能够辅导初中全科的大学生"""

EXPECTED_NUMBERS = ["温州100922", "温州100628", "温州100611", "温州092904", "温州092884", "温州092701", "S223080"]


def test_seven_orders_with_no_blank_line_between_first_two():
    segments = split_orders(SAMPLE_ORDERS)
    assert len(segments) == 7
    assert [parse_local(s.raw_text).order_number for s in segments] == EXPECTED_NUMBERS
    assert "温州100628" not in segments[0].raw_text
    assert "220-240/2h" in segments[0].raw_text
    assert "后续上的好的话70/h" in segments[1].raw_text


def test_fully_glued_numbered_orders_are_not_merged():
    glued = "温州100922【辅导科目】：高三数学【老师课费】：220-240/2h温州100628学员情况：初一辅导科目：数学英语老师薪水：240/4h"
    segments = split_orders(glued)
    assert len(segments) == 2
    assert segments[0].raw_text.endswith("220-240/2h")
    assert segments[1].raw_text.startswith("温州100628")


def test_numbered_and_unnumbered_orders_and_duplicate_numbers_are_kept():
    text = "温州100922\n辅导科目：高三数学\n\n学员情况：初一\n辅导科目：英语\n\n温州100922\n辅导科目：初二科学"
    segments = split_orders(text)
    assert len(segments) == 3
    assert segments[1].raw_text.startswith("学员情况")
    assert segments[0].raw_text.startswith("温州100922")
    assert segments[2].raw_text.startswith("温州100922")


def test_ambiguous_block_is_checked_even_if_batch_has_blank_lines():
    text = "学员情况：高三\n辅导科目：数学\n老师薪水：220/2h\n学员情况：初一\n辅导科目：英语\n老师薪水：240/4h\n\nS223080【初中全科】180/2h"
    segments = split_orders(text)
    assert len(segments) == 2
    assert segments[0].uncertain
    assert not segments[1].uncertain
    second_start = segments[0].raw_text.index("学员情况：初一")
    with patch("tutor_screen.pipeline.try_split", return_value=[0, second_start]) as split, patch("tutor_screen.pipeline.extract_order", side_effect=ValueError("offline")):
        orders = process_batch(text, "dummy", "https://example.test", "dummy")
    split.assert_called_once()
    assert len(orders) == 3
    assert orders[0].split_uncertain and orders[1].split_uncertain
    assert orders[1].raw_text.startswith("学员情况：初一")


def test_single_order_with_many_fields_does_not_need_model_splitting():
    text = "订单号 A100\n年级：小学\n科目：数学\n时间安排：周六晚上\n时薪：150\n学员地址：模拟小区"
    segments = split_orders(text)
    assert len(segments) == 1 and not segments[0].uncertain


def test_seven_orders_survive_extraction_failure_without_losing_originals():
    with patch("tutor_screen.pipeline.extract_order", side_effect=ValueError("offline")), patch("tutor_screen.pipeline.try_split") as split:
        orders = process_batch(SAMPLE_ORDERS, "dummy", "https://example.test", "dummy")
    split.assert_not_called()
    assert len(orders) == 7
    assert [o.order_number for o in orders] == EXPECTED_NUMBERS
    assert [o.order_id for o in orders] == [f"ORD-{i:04d}" for i in range(1, 8)]


def test_over_limit_is_rejected_before_any_model_calls():
    text = "\n".join(f"温州{i:06d}\n辅导科目：小学数学" for i in range(301))
    with patch("tutor_screen.pipeline.try_split") as split, patch("tutor_screen.pipeline.extract_order") as extract:
        with pytest.raises(TooManyOrders):
            process_batch(text, "dummy", "https://example.test", "dummy")
    split.assert_not_called()
    extract.assert_not_called()


def fake_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))])


def test_model_preprocessing_maps_line_anchors_and_masks_private_fields():
    text = "学员情况：高三\r\n辅导科目：数学\r\n学员地址：测试路88号\r\n电话：13800138000\r\n老师薪水：220/2h\r\n学员情况：初一\r\n辅导科目：英语\r\n老师薪水：240/4h"
    response = {"boundaries": [{"line": 1, "anchor": "学员情况：高三"}, {"line": 6, "anchor": "学员情况：初一"}]}
    with patch("tutor_screen.llm_client._client") as client, patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        client.return_value.chat.completions.create.return_value = fake_response(response)
        orders = process_batch(text, "dummy", "https://example.test", "dummy")
        sent = client.return_value.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "L0001\t学员情况：高三" in sent
    assert "L0006\t学员情况：初一" in sent
    assert "测试路88号" not in sent and "13800138000" not in sent
    assert len(orders) == 2
    assert orders[0].raw_text == text[:text.index("学员情况：初一")].strip()
    assert orders[1].raw_text == text[text.index("学员情况：初一"):]
    assert all(o.split_uncertain for o in orders)
    assert all(match_order(o, Criteria()).match_status != "明确匹配" for o in orders)


def test_model_can_split_two_orders_within_one_line_without_character_counting():
    text = "学员情况：高三；辅导科目：数学；老师薪水：220/2h；学员情况：初一；辅导科目：英语；老师薪水：240/4h"
    response = {"boundaries": [{"line": 1, "anchor": "学员情况：高三"}, {"line": 1, "anchor": "学员情况：初一"}]}
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = fake_response(response)
        starts = try_split(text, "dummy", "https://example.test", "dummy")
    assert starts == [0, text.index("学员情况：初一")]


@pytest.mark.parametrize("boundaries", [
    [OrderBoundary(line=99, anchor="学员情况")],
    [OrderBoundary(line=1, anchor="模型臆造的文本")],
    [OrderBoundary(line=1, anchor="学员情况")],  # 同一行出现两次，不唯一。
    [OrderBoundary(line=1, anchor="学员情况：初一")],  # 缺少第一条。
    [OrderBoundary(line=1, anchor="学员情况：高三"), OrderBoundary(line=1, anchor="学员情况：高三")],
])
def test_unverifiable_model_boundaries_are_rejected(boundaries):
    text = "学员情况：高三；学员情况：初一"
    with pytest.raises(ValueError):
        resolve_model_boundaries(text, mask_for_model(text), boundaries)


def test_invalid_model_reply_retains_entire_original_for_review():
    text = "学员情况：高三\n辅导科目：数学\n学员情况：初一\n辅导科目：英语"
    with patch("tutor_screen.llm_client._client") as client, patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        client.return_value.chat.completions.create.return_value = fake_response({"boundaries": [{"line": 99, "anchor": "不存在"}]})
        orders = process_batch(text, "dummy", "https://example.test", "dummy")
    assert len(orders) == 1 and orders[0].raw_text == text
    assert orders[0].split_uncertain
    assert match_order(orders[0], Criteria()).match_status == "可能匹配"
