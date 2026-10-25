"""导入页填写条件后直接解析，以及页面间草稿、结果和重置的协作。"""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen import history_store, map_store
from tutor_screen.local_parser import parse_local
from tutor_screen.maps import GeoPoint
from tutor_screen.models import Order


APP = Path(__file__).resolve().parents[2] / "app.py"
RAW = "订单号 IMP101 小学数学 100/h 线上 周六晚上 每周1次\n订单号 IMP102 高中英语 200/h 线上 周六晚上 每周1次"


def test_import_renders_filter_card_after_editor_and_results_link_back():
    app = AppTest.from_file(APP).run()
    assert not app.exception
    assert [item.value for item in app.subheader] == ["添加订单", "筛选条件"]
    assert not app.container(key="import_editor").button
    assert not {"step_settings", "step_import", "step_results", "clear_orders", "start_processing"}.intersection(item.key for item in app.button)
    assert app.container(key="filter_panel").button(key="apply_filters") is not None
    app.radio(key="nav_widget").set_value("筛选结果").run()
    assert not any(item.key == "filter_panel" for item in app.container)
    app.button(key="edit_filters").click().run()
    assert not app.exception and app.session_state["page"] == "订单导入"
    assert app.container(key="filter_panel") is not None


def test_apply_parses_new_input_using_current_business_and_origin_drafts():
    origin = map_store.save_origin("合成出发点", GeoPoint(lng=120.7, lat=28, source="user"))
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["api_key"] = "synthetic-model"
    app.run()
    app.text_area(key="order_text").set_value(RAW).run()
    app.multiselect(key="filter_grades").set_value(["高中"]).run()
    app.number_input(key="filter_min_rate").set_value(150.0).run()
    app.selectbox(key="map_origin_draft").set_value(origin.id).run()
    assert app.session_state["criteria"].grades == []
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)) as model:
        app.button(key="apply_filters").click().run()
    assert model.call_count == 2 and not app.exception
    assert app.session_state["page"] == "筛选结果"
    assert len(app.session_state["orders"]) == 2
    assert [value.order_number for value in app.session_state["filtered"]] == ["IMP102"]
    assert app.session_state["criteria"].min_hourly_rate == 150
    assert app.session_state["map_applied"]["origin_id"] == origin.id
    app.button(key="edit_filters").click().run()
    assert app.multiselect(key="filter_grades").value == ["高中"]
    assert app.number_input(key="filter_min_rate").value == 150
    with patch("tutor_screen.pipeline.extract_order") as model:
        app.button(key="reset_filters").click().run()
        assert not app.exception and len(app.session_state["filtered"]) == 2
        assert app.multiselect(key="filter_grades").value == []
        assert not app.container(key="map_filter_controls").checkbox
        model.assert_not_called()


def test_legacy_distance_draft_without_origin_does_not_block_import():
    app = AppTest.from_file(APP)
    app.session_state["api_key"] = "synthetic-model"
    app.session_state["map_radius_enabled"] = True
    app.session_state["map_radius_km"] = 1.0
    app.session_state["map_hide_unknown"] = True
    app.run()
    app.text_area(key="order_text").set_value(RAW).run()
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)) as model:
        app.button(key="apply_filters").click().run()
    assert not app.exception and model.call_count == 2
    assert app.session_state["page"] == "筛选结果"
    assert len(app.session_state["filtered"]) == 2
    assert not any("出发点" in item.value for item in app.warning)


def test_unapplied_import_draft_does_not_change_existing_results_on_navigation():
    app = AppTest.from_file(APP)
    value = Order(order_id="OLD", raw_text="小学数学 100/h 线上", grade="小学", subjects=["数学"], hourly_rate=100)
    app.session_state["orders"] = [value]
    app.session_state["filtered"] = [value]
    app.run()
    app.number_input(key="filter_min_rate").set_value(150.0).run()
    app.radio(key="nav_widget").set_value("筛选结果").run()
    assert not app.exception and len(app.session_state["filtered"]) == 1
    app.button(key="edit_filters").click().run()
    assert app.number_input(key="filter_min_rate").value == 150
    with patch("tutor_screen.pipeline.extract_order") as model:
        app.button(key="apply_filters").click().run()
        assert not app.exception and app.session_state["filtered"] == []
        model.assert_not_called()


@pytest.mark.parametrize("uploaded", [False, True])
def test_applying_again_to_unchanged_text_or_upload_never_reparses_or_duplicates_import(uploaded):
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["api_key"] = "synthetic-model"
    if uploaded:
        app.session_state["uploaded_order_file"] = {"name": "orders.txt", "content": RAW.encode("utf-8")}
    else:
        app.session_state["order_text"] = RAW
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)) as model:
        app.run()
        app.button(key="apply_filters").click().run()
        assert model.call_count == 2 and not app.exception
    assert history_store.count_imports() == 1
    app.button(key="edit_filters").click().run()
    with patch("tutor_screen.pipeline.process_batch") as process:
        app.number_input(key="filter_min_rate").set_value(150.0).run()
        app.button(key="apply_filters").click().run()
        assert not app.exception and app.session_state["page"] == "筛选结果"
        assert app.title[0].value == "筛选结果" and app.radio(key="nav_widget").value == "筛选结果"
        app.button(key="edit_filters").click().run()
        app.button(key="apply_filters").click().run()
        process.assert_not_called()
    assert not app.exception and app.session_state["page"] == "筛选结果"
    assert [order.order_number for order in app.session_state["filtered"]] == ["IMP102"]
    assert history_store.count_imports() == 1
    assert not any(item.label == "确认覆盖并处理" for item in app.button)


@pytest.mark.parametrize("threshold,remaining", [(50, 1), (150, 0)])
def test_existing_orders_without_new_input_jump_to_results_even_when_all_excluded(threshold, remaining):
    raw = "小学数学 100/h 线上 周六晚上 每周1次"
    value = Order(**parse_local(raw).model_dump(), order_id="EXISTING", raw_text=raw)
    app = AppTest.from_file(APP)
    app.session_state["orders"] = [value]
    app.session_state["filtered"] = [value]
    app.run()
    with patch("tutor_screen.pipeline.process_batch") as model:
        app.number_input(key="filter_min_rate").set_value(float(threshold)).run()
        app.button(key="apply_filters").click().run()
        model.assert_not_called()
    assert not app.exception and app.title[0].value == "筛选结果"
    assert app.session_state["page"] == app.radio(key="nav_widget").value == "筛选结果"
    assert len(app.session_state["filtered"]) == remaining


def test_existing_orders_apply_without_origin_or_distance_controls():
    raw = "小学数学 100/h 线上"
    value = Order(**parse_local(raw).model_dump(), order_id="EXISTING", raw_text=raw)
    app = AppTest.from_file(APP)
    app.session_state["orders"] = [value]
    app.session_state["filtered"] = [value]
    app.run()
    controls = app.container(key="map_filter_controls")
    assert not controls.checkbox and not controls.number_input
    assert {item.label for item in controls.selectbox} == {"出发点", "订单排序"}
    with patch("tutor_screen.pipeline.process_batch") as model:
        app.button(key="apply_filters").click().run()
        model.assert_not_called()
    assert not app.exception and app.session_state["page"] == "筛选结果"
    assert len(app.session_state["filtered"]) == 1
    assert not any("出发点" in warning.value for warning in app.warning)


def test_legacy_batch_recognizes_original_input_even_with_unparsed_batch_heading():
    raw = "家教单\n" + RAW
    record_id = history_store.create_import(source_type="粘贴", input_text=raw)
    orders = [Order(**parse_local(text).model_dump(), order_id=f"OLD-{index}", raw_text=text)
              for index, text in enumerate(RAW.splitlines())]
    app = AppTest.from_file(APP)
    app.session_state["order_text"] = raw
    app.session_state["orders"] = orders
    app.session_state["filtered"] = orders
    app.session_state["current_import_id"] = record_id
    with patch("tutor_screen.pipeline.process_batch") as process:
        app.run()
        app.number_input(key="filter_min_rate").set_value(150.0).run()
        app.button(key="apply_filters").click().run()
        process.assert_not_called()
    assert not app.exception
    assert [order.order_number for order in app.session_state["filtered"]] == ["IMP102"]
    assert history_store.count_imports() == 1
