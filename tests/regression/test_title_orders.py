"""标题式地址与用户订单全链路回归。"""
import csv
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen.export import orders_csv
from tutor_screen.llm_client import extract_order, try_split
from tutor_screen.local_parser import parse_local
from tutor_screen.matching import apply_criteria, match_order
from tutor_screen.models import Criteria, ExtractedOrder, Order
from tutor_screen.pipeline import process_batch
from tutor_screen.privacy import mask_for_model
from tutor_screen.segmentation import split_orders
from helpers import order, response
from title_order_samples import BATCH, EXPECTED, NOTICE, RAWS

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts" / "regression" / "title_orders"


@pytest.mark.parametrize("index", range(5), ids=[f"USER-{i+1}" for i in range(5)])
def test_all_user_fields(index):
    actual = parse_local(RAWS[index])
    expected = EXPECTED[index]
    assert {name: getattr(actual, name) for name in expected} == expected


@pytest.mark.parametrize("separator", ["\n\n", "\n", ""])
def test_headers_and_tail_numbers_split_without_losing_orders(separator):
    raw = "\n\n".join([NOTICE, separator.join(RAWS)])
    pieces = split_orders(raw)
    assert [piece.raw_text for piece in pieces] == RAWS
    assert not any(piece.uncertain for piece in pieces)


@pytest.mark.parametrize("left,right", [("【", "】"), ("〖", "〗"), ("[", "]"), ("〔", "〕"), ("［", "］"), ("「", "」")])
@pytest.mark.parametrize("separator", ["，", ",", "；"])
def test_title_address_variants(left, right, separator):
    raw = f"S123456{left}小学数学{separator}鹿城模拟组团2区{right}四年级女生，周六晚上，150/h"
    result = parse_local(raw)
    assert result.address == "鹿城模拟组团2区"
    assert result.subjects == ["数学"] and result.teacher_gender == "不限"
    masked = mask_for_model(raw)
    assert "鹿城模拟组团2区" not in masked
    assert "四年级女生" in masked and "周六晚上" in masked


def test_explicit_address_wins_over_title_reference():
    raw = "【初中数学，龙湾万达旁边】\n地址：龙湾模拟住宅8栋\n时间：周六晚上"
    assert parse_local(raw).address == "龙湾模拟住宅8栋"
    masked = mask_for_model(raw)
    assert "龙湾万达旁边" not in masked and "龙湾模拟住宅8栋" not in masked


@pytest.mark.parametrize("raw", ["【初中数学】初三女生，150/h", "【初中数学，基础薄弱】初三女生，150/h", "【初中数学，线上】初三女生，150/h", "科目：数学，要求女老师"])
def test_non_location_header_text_is_not_an_address(raw):
    assert parse_local(raw).address is None


def test_vague_location_keeps_raw_and_requires_confirmation():
    parsed = parse_local(RAWS[2])
    assert parsed.address == "龙湾万达旁边" and "地址范围" in parsed.uncertain_fields
    assert parsed.field_sources["address"] in RAWS[2]
    assert "温州市" not in parsed.address


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("path", ["extract", "split"])
def test_actual_requests_hide_title_location_but_keep_order_content(index, path):
    raw = RAWS[index]
    address = EXPECTED[index]["address"]
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = response({} if path == "extract" else {"boundaries": []})
        (extract_order if path == "extract" else try_split)(raw, "dummy-key", "https://example.test", "model")
        sent = client.return_value.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
    assert address not in sent
    assert ["初三女生", "初3女", "高1女", "新初3男", "新6年级女"][index] in sent
    assert ["一周一次", "一周五次", "一周一次", "一周五次", "一周一次课"][index] in sent
    masked = mask_for_model(raw)
    assert len(masked) == len(raw)
    assert [i for i, char in enumerate(masked) if char == "\n"] == [i for i, char in enumerate(raw) if char == "\n"]


def test_batch_model_local_matching_csv_agree_on_addresses():
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda *_: ExtractedOrder(grade="高中", subjects=["数学"], hourly_rate=999, teacher_gender="不限", time_slots=["下午"])) as model, patch("tutor_screen.pipeline.try_split") as splitter:
        orders = process_batch(BATCH, "key", "https://example.test", "model")
    splitter.assert_not_called()
    assert model.call_count == 5 and [value.raw_text for value in orders] == RAWS
    assert len({value.order_id for value in orders}) == 5
    for index, value in enumerate(orders):
        assert {field: getattr(value, field) for field in EXPECTED[index]} == EXPECTED[index]
    female = apply_criteria(orders, Criteria(teacher_gender="女"))
    male = apply_criteria(orders, Criteria(teacher_gender="男"))
    assert len(female) == 5 and [value.raw_text for value in male] == RAWS[3:]
    rows = list(csv.DictReader(io.StringIO(orders_csv(female).decode("utf-8-sig"))))
    assert [row["address"] for row in rows] == [item["address"] for item in EXPECTED]
    assert [row["raw_text"] for row in rows] == RAWS


def test_old_session_refilter_repairs_fields_without_model_calls():
    old = [Order(order_id=f"ORD-{i}", raw_text=raw, grade="初中", subjects=["科学"], mode="未知", teacher_gender="不限", time_slots=["下午"], lesson_hours=2, extraction_source="model", uncertain_fields=["mode", "上课方式"]) for i, raw in enumerate(RAWS)]
    app = AppTest.from_file(Path(__file__).parents[2] / "app.py", default_timeout=15)
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = old
    app.session_state["filtered"] = old
    app.run()
    with patch("tutor_screen.llm_client._client") as model:
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.button(key="apply_filters").click().run()
    assert not app.exception
    model.assert_not_called()
    result = app.session_state["filtered"]
    assert [value.address for value in result] == [item["address"] for item in EXPECTED]
    assert result[0].time_slots == [] and result[4].lesson_hours is None
    assert all("mode" not in value.uncertain_fields for value in result)
    app.radio(key="nav_widget").set_value("筛选结果").run()
    assert all(any(item["address"] in caption.value for caption in app.caption) for item in EXPECTED)
    assert any("1-3点" in caption.value and "排除" in caption.value for caption in app.caption)


def test_only_notice_is_empty_but_unrecognized_content_is_preserved():
    assert split_orders(NOTICE) == []
    assert [value.raw_text for value in split_orders("这是陌生格式，需要人工复核")] == ["这是陌生格式，需要人工复核"]
    with patch("tutor_screen.pipeline.extract_order") as model:
        assert process_batch(NOTICE, "key", "https://example.test", "model") == []
    model.assert_not_called()


def test_no_blank_notice_is_not_attached_to_first_order():
    assert [value.raw_text for value in split_orders(NOTICE + "\n" + "\n".join(RAWS))] == RAWS
