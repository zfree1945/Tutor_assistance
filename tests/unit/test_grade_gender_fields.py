"""年级性别复合字段：使用截图格式与合成地点，不复制真实订单。"""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen.local_parser import parse_local
from tutor_screen.matching import evaluate_order
from tutor_screen.models import Criteria, ExtractedOrder, Order
from tutor_screen.pipeline import process_batch
from tutor_screen.privacy import mask_for_model
from tutor_screen.text_fields import read_fields


def sample(grade="初一，女", student="数学基础薄弱，英语有待提高", address="测试街道示例花苑"):
    return (
        "【温州家教 90000001】\n"
        f"联系地址：{address}\n"
        f"年级性别：{grade}\n"
        "辅导科目：数学,英语\n"
        f"学员情况：{student}\n"
        "时间安排：一周1次，一次2小时，周六上午\n"
        "教员要求：男女皆可，有家教经验，有耐心负责\n"
        "薪资待遇：70元/小时"
    )


@pytest.mark.parametrize("grade,student,expected", [
    ("初一，男孩子", "准8年级男孩，补理科，平时30-40分", "初中"),
    ("初一，女", "数学基础薄弱，英语有待提高", "初中"),
    ("六年级，男孩子", "新概念第一册背诵到二十多课，主要教语法辅导作业", "小学"),
    ("高一，男", "大题感觉没思路做不出来", "高中"),
])
def test_screenshot_grade_field_is_recognized_and_separated_from_address(grade, student, expected):
    raw = sample(grade, student)
    parsed = parse_local(raw)
    assert parsed.grade == expected
    assert "年级" not in parsed.uncertain_fields
    assert parsed.address == "测试街道示例花苑"
    assert "年级性别" not in parsed.field_sources["address"]
    assert parsed.teacher_gender == "不限"
    assert parsed.hourly_rate == 70
    assert parsed.subjects == ["数学", "英语"]


@pytest.mark.parametrize("label", ["年级性别", "年级"])
@pytest.mark.parametrize("brackets", [None, ("【", "】"), ("〖", "〗"), ("[", "]")])
def test_explicit_grade_takes_priority_over_student_description(label, brackets):
    heading = f"{brackets[0]}{label}{brackets[1]}" if brackets else label
    # 显式年级放在前后都优先，不能被学员情况里的“初中内容”覆盖。
    for raw in [f"学员情况：学习初中内容\n{heading}：六年级，男\n辅导科目：英语",
                f"{heading}：六年级，男\n学员情况：学习初中内容\n辅导科目：英语"]:
        parsed = parse_local(raw)
        assert parsed.grade == "小学"
        assert parsed.field_sources["grade"] == "六年级"


def test_fields_and_model_mask_keep_grade_while_hiding_the_address():
    raw = sample("六年级，男孩子")
    fields = read_fields(raw)
    grade = next(field for field in fields if field.category == "grade")
    address = next(field for field in fields if field.category == "address")
    assert grade.value == "六年级，男孩子"
    assert address.value == "测试街道示例花苑"
    masked = mask_for_model(raw)
    assert len(masked) == len(raw)
    assert "测试街道示例花苑" not in masked
    assert "年级性别：六年级，男孩子" in masked


@pytest.mark.parametrize("model_fails", [False, True])
def test_import_corrects_model_grade_and_filters_using_local_grade(model_fails):
    raw = sample("高一，男", "大题感觉没思路做不出来")
    with patch("tutor_screen.pipeline.extract_order", side_effect=(
        RuntimeError("offline") if model_fails else lambda *_: ExtractedOrder(grade="小学")
    )):
        orders = process_batch(raw, "synthetic", "https://example.test", "offline")
    assert len(orders) == 1 and orders[0].grade == "高中"
    assert "年级" not in orders[0].uncertain_fields
    assert evaluate_order(orders[0], Criteria(grades=["高中"])).match_status != "已排除"
    assert evaluate_order(orders[0], Criteria(grades=["小学", "初中"])).match_status == "已排除"


def test_unknown_grade_stays_unknown_instead_of_using_teacher_grade():
    raw = sample("待定，男", "基础薄弱").replace("教员要求：男女皆可", "教员要求：有高中教学经验，男女皆可")
    parsed = parse_local(raw)
    assert parsed.grade is None and "年级" in parsed.uncertain_fields


def test_online_address_does_not_swallow_grade():
    parsed = parse_local(sample("六年级，男孩子", address="线上辅导"))
    assert parsed.grade == "小学"
    assert parsed.mode == "线上" and parsed.address is None
    assert "地址待确认" not in parsed.uncertain_fields


def test_results_page_upgrades_old_batch_and_grade_filter_without_model_calls():
    raw = sample("六年级，男孩子")
    old = Order(order_id="OLD-GRADE", raw_text=raw, grade=None, uncertain_fields=["年级", "grade"],
                address="测试街道示例花苑 年级性别：六年级，男孩子", extraction_source="model")
    app = AppTest.from_file(Path(__file__).resolve().parents[2] / "app.py", default_timeout=15)
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [old]
    app.session_state["filtered"] = [old]
    app.session_state["_history_cleared"] = True
    app.session_state["_local_fields_revision"] = "2026-10-17-compact-pricing"
    with patch("tutor_screen.llm_client._client") as client:
        app.run()
        assert not app.exception
        repaired = app.session_state["filtered"][0]
        assert repaired.grade == "小学" and repaired.address == "测试街道示例花苑"
        assert not {"年级", "grade"}.intersection(repaired.uncertain_fields)
        assert any("小学" in item.value and "数学" in item.value for item in app.markdown)
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.multiselect(key="filter_grades").set_value(["高中"]).run()
        app.button(key="apply_filters").click().run()
        assert not app.exception and app.session_state["filtered"] == []
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.multiselect(key="filter_grades").set_value(["小学"]).run()
        app.button(key="apply_filters").click().run()
        assert not app.exception and app.session_state["filtered"][0].grade == "小学"
    client.assert_not_called()
