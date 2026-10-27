"""扩展反例：修复范围附近的否定、残缺值与旧会话不能错误放行。"""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen.llm_client import ModelReplyError, extract_order
from tutor_screen.local_parser import parse_local
from tutor_screen.matching import match_order
from tutor_screen.models import Criteria, ExtractedOrder
from tutor_screen.pipeline import process_batch
from tutor_screen.pricing import parse_price
from tutor_screen.segmentation import split_orders
from helpers import order, response
from title_order_samples import NOTICE


@pytest.mark.parametrize("quote", ["200元两小时半", "200/两小时半", "200/两小时三十分钟"])
def test_price_tail_minutes_are_not_dropped(quote):
    assert parse_price(quote).hourly_rate == 80


@pytest.mark.parametrize("clock", ["周六23:00-01:00", "周六25:00-26:00", "周六19:75-21:30", "周六19:00-19:00"])
def test_bad_or_cross_day_clock_keeps_uncertainty_and_clears_model_slot(clock):
    raw = "小学数学 150/h 线上 每周1次\n时间：" + clock
    with patch("tutor_screen.pipeline.extract_order", return_value=ExtractedOrder(time_slots=["晚上"])):
        value = process_batch(raw, "key", "https://example.test", "model")[0]
    assert value.time_slots == [] and "钟点时间" in value.uncertain_fields
    assert match_order(value, Criteria(time_slots=["晚上"])).match_status == "可能匹配"


@pytest.mark.parametrize("schedule", ["周六不行，周日上午可以", "周六晚上不行，周日上午可以"])
def test_negative_weekday_does_not_become_a_required_fixed_day(schedule):
    value = order("小学数学 150/h 线上 每周1次\n时间：" + schedule)
    assert value.days == [7]
    assert match_order(value, Criteria(days=[7], time_slots=["上午"])) is not None
    assert match_order(value, Criteria(days=[6])) is None


def test_explicit_blocked_day_is_not_accepted_as_the_only_available_day():
    value = order("小学数学 150/h 线上 每周1次\n时间：周六不行，其他日子可协商")
    assert value.days == []
    assert getattr(value, "excluded_days", []) == [6]
    assert match_order(value, Criteria(days=[6])) is None
    assert match_order(value, Criteria(days=[7])).match_status == "可能匹配"


def test_full_slot_exclusion_is_a_hard_mismatch_but_partial_window_is_not():
    for weekday in ("周六", "周6", "星期6"):
        full = order(f"小学数学 150/h 线上 每周1次\n时间：{weekday}晚上不行，其他时间可协商")
        for days in ([6], [6, 7], []):
            assert match_order(full, Criteria(days=days, time_slots=["晚上"])) is None, weekday
        assert match_order(full, Criteria(days=[6], time_slots=["上午"])) is not None
    partial = order("小学数学 150/h 线上 每周1次\n时间：周六晚上19:00-20:00不行，其他时间可协商")
    assert match_order(partial, Criteria(days=[6], time_slots=["晚上"])).match_status == "可能匹配"


def test_valid_model_exclusions_have_strict_day_schema():
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = response({"days": [7], "excluded_days": [6], "excluded_times": ["周六不行"]})
        value = extract_order("小学数学", "key", "https://example.test", "model")
    assert value.days == [7] and value.excluded_days == [6]


def test_school_subject_word_in_location_does_not_add_a_subject():
    parsed = parse_local("【小学数学，鹿城示例城小区】四年级，每周1次，周六晚上，150/h")
    assert parsed.subjects == ["数学"] and parsed.address == "鹿城示例城小区"


def test_two_glued_subject_headers_without_address_still_split():
    raws = ["【初中数学】初三女生，150/h", "【高中物理】高一男生，200/h"]
    assert [item.raw_text for item in split_orders("".join(raws))] == raws


def test_notice_keywords_cannot_discard_real_order_text():
    raw = "#诚招小学数学家教，150/h，周六晚上"
    assert [value.raw_text for value in split_orders(raw)] == [raw]


def test_conflicting_gender_cannot_be_overridden_by_model_certainty():
    raw = "小学数学 150/h 线上 周六晚上\n要求：男老师，女老师，具体未定"
    with patch("tutor_screen.pipeline.extract_order", return_value=ExtractedOrder(teacher_gender="女")):
        value = process_batch(raw, "key", "https://example.test", "model")[0]
    assert value.teacher_gender == "未知"
    assert match_order(value, Criteria(teacher_gender="男")).match_status == "可能匹配"


def test_notice_only_import_reports_no_orders_and_keeps_input():
    app = AppTest.from_file(Path(__file__).parents[2] / "app.py", default_timeout=15)
    app.session_state["api_key"] = "dummy-key"
    app.run()
    app.text_area(key="order_text").set_value(NOTICE).run()
    app.button(key="apply_filters").click().run()
    assert not app.exception and app.session_state["page"] == "订单导入"
    assert app.text_area(key="order_text").value == NOTICE
    assert any("未识别到订单" in value.value for value in app.warning)


@pytest.mark.parametrize("payload", [{"excluded_days": [8]}, {"excluded_days": [True]}, {"days": [6], "excluded_days": [6]}])
def test_invalid_or_contradictory_excluded_days_reject_model(payload):
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = response(payload)
        with pytest.raises(ModelReplyError):
            extract_order("小学数学", "key", "https://example.test", "model")
