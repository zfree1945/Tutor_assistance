import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen.llm_client import ModelReplyError, extract_order
from tutor_screen.local_parser import parse_local
from tutor_screen.matching import match_order
from tutor_screen.models import Criteria, ExtractedOrder, Order
from tutor_screen.pipeline import process_batch
from tutor_screen.pricing import parse_price
from tutor_screen.privacy import mask_for_model
from tutor_screen.segmentation import split_orders


ORDER_54 = """温州59433287
〖地址〗龙港市-合成大道模拟小区东苑
〖学员〗四年级女生
〖科目〗语文
〖情况〗阅读理解
〖时间〗一周1次，一次1.5小时，时间可协商
〖要求〗女专职老师，有经验认真负责，需要认真负责，上课不要太死板
〖待遇〗上门150元一个小时
线上辅导130一个小时（本地）"""
ORDER_45 = """温州59433278
〖地址〗永嘉县上塘镇模拟大厦
〖学员〗初三男
〖科目〗科学数学英语
〖情况〗巩固提升
〖时间〗一周一次，一次3小时，周六或者周日
〖要求〗女大学生，一本或以上
〖待遇〗100元一个小时"""


def as_order(text, source="model"):
    return Order(**parse_local(text).model_dump(), order_id="ORD-1", raw_text=text, extraction_source=source)


def test_first_screenshot_fields_are_recognized_locally():
    parsed = parse_local(ORDER_54)
    assert parsed.grade == "小学"
    assert parsed.subjects == ["语文"]
    assert (parsed.hourly_rate_min, parsed.hourly_rate_max) == (130, 150)
    assert not parsed.price_incomplete
    assert parsed.mode == "线上/线下均可"
    assert parsed.teacher_gender == "女"
    assert parsed.weekly_sessions == 1
    assert parsed.lesson_hours == 1.5
    assert parsed.days == [] and parsed.time_slots == []
    assert not parsed.address.startswith("〗")
    assert "时薪" not in parsed.uncertain_fields and "每周次数" not in parsed.uncertain_fields
    assert "星期" in parsed.uncertain_fields and "时间段" in parsed.uncertain_fields


def test_second_screenshot_does_not_invent_monday_or_a_time_slot():
    parsed = parse_local(ORDER_45)
    assert parsed.grade == "初中"
    assert set(parsed.subjects) == {"科学", "数学", "英语"}
    assert parsed.hourly_rate == 100
    assert parsed.weekly_sessions == 1
    assert parsed.lesson_hours == 3
    assert parsed.days == [6, 7]
    assert parsed.day_options == [[6], [7]]
    assert parsed.time_slots == []
    assert parsed.teacher_gender == "女" and parsed.mode == "线下"
    assert "时薪" not in parsed.uncertain_fields and "每周次数" not in parsed.uncertain_fields


@pytest.mark.parametrize("start,end", [("〖", "〗"), ("【", "】"), ("[", "]"), ("〔", "〕"), ("［", "］"), ("「", "」")])
def test_bracket_formats_are_parsed_and_address_is_masked(start, end):
    raw = f"温州59433287\n{start}地址{end}测试小区东苑\n{start}学员{end}四年级女生\n{start}科目{end}语文"
    parsed = parse_local(raw)
    assert parsed.address == "测试小区东苑"
    assert parsed.grade == "小学"
    masked = mask_for_model(raw)
    assert "测试小区东苑" not in masked
    assert len(masked) == len(raw)


@pytest.mark.parametrize("frequency,count", [("一周1次", 1), ("一周一次", 1), ("每周一次", 1), ("每周2次", 2), ("一周两次", 2), ("每星期三次", 3), ("一周10次", 10)])
def test_weekly_frequency_is_not_misread_as_a_weekday(frequency, count):
    parsed = parse_local(f"〖时间〗{frequency}，周六或者周日，每次3小时")
    assert parsed.weekly_sessions == count
    assert parsed.days == [6, 7]
    assert parsed.lesson_hours == 3


def test_weekday_ranges_and_alternatives_have_different_matching_rules():
    range_order = parse_local("〖时间〗一周5次，周一到周五晚上")
    assert range_order.days == [1, 2, 3, 4, 5]
    weekend = as_order(ORDER_45)
    assert match_order(weekend, Criteria(days=[6])) is not None
    assert match_order(weekend, Criteria(days=[7])) is not None
    assert match_order(weekend, Criteria(days=[1])) is None
    fixed = as_order("初中数学 线上 100/h\n〖时间〗每周2次，周五周六晚上")
    assert match_order(fixed, Criteria(days=[6])) is None
    assert match_order(fixed, Criteria(days=[5, 6])) is not None


def test_any_number_of_known_quotes_are_combined_into_one_hourly_range():
    raw = "小学数学 线上\n〖时间〗每周1次，周六晚上\n〖待遇〗150元一个小时\n〖待遇〗130一个小时\n〖待遇〗300元2小时\n〖待遇〗200元每小时"
    price = parse_price(raw)
    assert (price.lower, price.upper) == (130, 200)
    assert not price.has_unknown
    assert len(split_orders(raw)) == 1 and not split_orders(raw)[0].uncertain
    order = as_order(raw)
    assert match_order(order, Criteria(min_hourly_rate=100)).match_status == "明确匹配"
    possible = match_order(order, Criteria(min_hourly_rate=150))
    assert possible.match_status == "可能匹配" and "时薪范围" in possible.uncertain_fields
    assert match_order(order, Criteria(min_hourly_rate=210)) is None


def test_unknown_quote_does_not_erase_known_hourly_range():
    order = as_order("小学语文 线上 150元一个小时，130一个小时，其他报价面议\n时间：每周1次，周六晚上")
    assert (order.hourly_rate_min, order.hourly_rate_max) == (130, 150)
    assert order.price_incomplete
    result = match_order(order, Criteria(min_hourly_rate=200))
    assert result is not None and result.match_status == "可能匹配"
    assert "部分薪资" in result.uncertain_fields


def test_null_and_pending_model_lists_are_accepted_as_unknown():
    payload = {"grade": "小学", "subjects": ["语文"], "days": None, "time_slots": "时间可协商", "uncertain_fields": None, "field_sources": None}
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))])
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = response
        parsed = extract_order(ORDER_54, "dummy", "https://example.test", "dummy")
    assert parsed.days == [] and parsed.time_slots == []
    assert parsed.field_sources == {}


def test_bad_model_records_do_not_disable_model_for_remaining_orders():
    raw = "\n\n".join(f"温州5943328{i}\n小学数学 线上 100/h 周六晚上" for i in range(4))
    with patch("tutor_screen.pipeline.extract_order", side_effect=[ModelReplyError("模型字段格式校验失败：时间段")] * 3 + [ExtractedOrder(grade="小学", subjects=["数学"])]) as extract:
        orders = process_batch(raw, "dummy", "https://example.test", "dummy")
    assert extract.call_count == 4
    assert [order.extraction_source for order in orders] == ["local", "local", "local", "model"]
    assert "时间段" in orders[0].extraction_note


def test_apply_filters_updates_old_batch_fields_without_new_model_requests():
    old = Order(order_id="ORD-0009", raw_text=ORDER_45, grade="初中", subjects=["科学", "数学", "英语"], days=[1, 6, 7], uncertain_fields=["时薪", "每周次数", "时间段"], extraction_source="local")
    app = AppTest.from_file(Path(__file__).resolve().parents[2] / "app.py")
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [old]
    app.session_state["filtered"] = [old]
    app.run()
    with patch("tutor_screen.llm_client._client") as client:
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.button(key="apply_filters").click().run()
    assert not app.exception
    client.assert_not_called()
    repaired = app.session_state["filtered"][0]
    assert repaired.days == [6, 7] and repaired.weekly_sessions == 1
    assert repaired.hourly_rate == 100 and repaired.lesson_hours == 3
    app.radio(key="nav_widget").set_value("筛选结果").run()
    assert any("每周 1 次" in caption.value and "每次 3 小时" in caption.value for caption in app.caption)
    assert any("周六 或 周日" in caption.value for caption in app.caption)
