"""加急标题、混合编号和星号字段的脱敏回归；不读取真实导入日志。"""

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen import batch_store, history_store
from tutor_screen.local_parser import parse_local
from tutor_screen.models import Criteria, Order
from tutor_screen.pipeline import process_batch, repair_merged_orders
from tutor_screen.privacy import mask_for_model
from tutor_screen.segmentation import split_orders


APP = Path(__file__).resolve().parents[2] / "app.py"
FIRST = """温州26101953
地址★测试镇示例学校附近
学员★高一 男孩
科目★数学
情况★月考需要巩固基础
时间★一周1次，一次2个小时，周日早上8-10点
要求★男大学生，有高中数学辅导经验
报酬★100一个小时"""
SECOND = """温州29101232 加急订单
地址★示例镇合成家园
学员★高二 男孩
科目★数学
情况★基础巩固提升
时间★一周1次，一次2个小时，周末，时间可协商
要求★男大学生，有高中数学辅导经验
报酬★90-100一个小时"""
THIRD = """温州线下ZYM26101902🍋
【年级科目】初三科学
【辅导地点】测试市示例广场购物中心
【课费报酬】100/h
【时间次数】一周一次，一次2h。
【学生情况】女孩
【老师要求】大学生"""
MERGED = FIRST + "\n\n" + SECOND + "\n" + THIRD
NUMBERS = ["温州26101953", "温州29101232", "温州线下ZYM26101902"]


@pytest.mark.parametrize("separator", ["\n", "\n\n", "\r\n"])
def test_decorated_number_headers_split_without_a_model_even_without_blank_lines(separator):
    raw = separator.join([FIRST, SECOND, THIRD])
    segments = split_orders(raw)
    assert [segment.raw_text for segment in segments] == [FIRST, SECOND, THIRD]
    assert all(not segment.uncertain for segment in segments)
    assert [parse_local(segment.raw_text).order_number for segment in segments] == NUMBERS


@pytest.mark.parametrize("offline", [False, True])
def test_three_orders_keep_their_own_grade_subject_address_and_price_on_model_failure(offline):
    with patch("tutor_screen.pipeline.try_split") as split, patch("tutor_screen.pipeline.extract_order",
            side_effect=RuntimeError("offline") if offline else lambda raw, *_: parse_local(raw)):
        orders = process_batch(MERGED, "synthetic", "https://example.test", "model")
    split.assert_not_called()
    assert [order.order_number for order in orders] == NUMBERS
    assert [order.grade for order in orders] == ["高中", "高中", "初中"]
    assert [order.subjects for order in orders] == [["数学"], ["数学"], ["科学"]]
    assert [order.address for order in orders] == ["测试镇示例学校附近", "示例镇合成家园", "测试市示例广场购物中心"]
    assert [(order.hourly_rate_min, order.hourly_rate_max) for order in orders] == [(100, 100), (90, 100), (100, 100)]
    assert [order.raw_text for order in orders] == [FIRST, SECOND, THIRD]


@pytest.mark.parametrize("raw", [FIRST, SECOND, THIRD, FIRST.replace("★", "☆")])
def test_star_and_composite_field_addresses_are_masked_without_hiding_grade(raw):
    parsed = parse_local(raw)
    masked = mask_for_model(raw)
    assert parsed.address and parsed.address not in masked
    assert len(masked) == len(raw)
    assert ("高一" if "高一" in raw else "高二" if "高二" in raw else "初三") in masked


def test_unmarked_repeated_star_fields_still_request_boundary_review():
    first, second = FIRST.split("\n", 1)[1], SECOND.split("\n", 1)[1]
    segments = split_orders(first + "\n" + second)
    assert len(segments) == 1 and segments[0].uncertain


@pytest.mark.parametrize("header", ["wz2391♻️", "wz2383，缺英语", "WZ2390", "温州线下zym26101902🍋"])
def test_numbered_header_variants_keep_the_complete_identifier(header):
    raw = header + "\n信息：小学数学，100/h 线上"
    segments = split_orders(FIRST + "\n" + raw)
    assert len(segments) == 2
    assert parse_local(segments[1].raw_text).order_number == header.split("♻")[0].split("，")[0].split("🍋")[0]


def old_order():
    return Order(order_id="OLD", raw_text=MERGED, order_number=NUMBERS[0], grade="初中", subjects=["科学"],
                 hourly_rate_min=90, hourly_rate_max=100, extraction_source="local")


def test_saved_order_repair_keeps_other_model_results_and_avoids_id_collisions():
    unaffected = Order(order_id="OLD-S02", raw_text="订单号 KEEP 小学英语 线上", subjects=["英语"], extraction_source="model")
    with patch("tutor_screen.pipeline.extract_order") as model:
        repaired = repair_merged_orders([old_order(), unaffected])
        assert repair_merged_orders(repaired) == repaired
        model.assert_not_called()
    assert repaired[-1] is unaffected
    assert len({order.order_id for order in repaired}) == 4
    assert [order.order_number for order in repaired[:3]] == NUMBERS
    assert repaired[0].order_id == "OLD"


def test_number_references_and_footer_identifiers_do_not_split_single_orders():
    raw = "S223080【初中数学】线上 100/h\n备注：参考温州29101232 加急订单，仍是同一学员\nS188519-2"
    order = Order(order_id="ONE", raw_text=raw, extraction_source="model")
    assert len(split_orders(raw)) == 1
    assert repair_merged_orders([order]) == [order]


@pytest.mark.parametrize("from_disk", [False, True])
def test_existing_session_and_saved_batch_repair_without_reparsing_or_changing_input(from_disk):
    original = old_order()
    criteria = Criteria(min_hourly_rate=101)
    record_id = history_store.create_import(source_type="粘贴", input_text=MERGED)
    history_store.append_event(record_id, kind="解析完成", parsed_orders=[original])
    app = AppTest.from_file(APP, default_timeout=15)
    if from_disk:
        batch_store.save_batch(batch_store.SavedBatch(input_text=MERGED, orders=[original], criteria=criteria, import_id=record_id))
    else:
        app.session_state["page"] = "筛选结果"
        app.session_state["order_text"] = MERGED
        app.session_state["orders"] = [original]
        app.session_state["filtered"] = [original]
        app.session_state["criteria"] = criteria
        app.session_state["current_import_id"] = record_id
        app.session_state["_local_fields_revision"] = "old"
    with patch("tutor_screen.pipeline.process_batch") as batch, patch("tutor_screen.pipeline.extract_order") as model:
        app.run()
        assert not app.exception
        assert [order.order_number for order in app.session_state["orders"]] == NUMBERS
        assert app.session_state["filtered"] == []
        assert batch_store.load_batch().input_text == MERGED
        assert len(batch_store.load_batch().orders) == 3
        app.radio(key="nav_widget").set_value("订单导入").run()
        assert app.text_area(key="order_text").value == MERGED
        app.button(key="reset_filters").click().run()
        assert len(app.session_state["filtered"]) == 3
        batch.assert_not_called()
        model.assert_not_called()
