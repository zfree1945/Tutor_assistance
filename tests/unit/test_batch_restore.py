"""刷新/新会话恢复完整输入和解析结果；失败导入不能覆盖已保存批次。"""

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen import batch_store, history_store
from tutor_screen.local_parser import parse_local
from tutor_screen.models import Order


APP = Path(__file__).resolve().parents[2] / "app.py"
RAW = "本批订单\n订单号 SAVE101 小学数学 100/h 线上 周六晚上 每周1次\n订单号 SAVE102 高中英语 200/h 线上 周日晚上 每周1次\n"


def parse_batch(*, uploaded=False):
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["api_key"] = "SYNTHETIC-KEY"
    if uploaded:
        app.session_state["uploaded_order_file"] = {"name": "synthetic.txt", "content": RAW.encode("utf-8-sig")}
    else:
        app.session_state["order_text"] = RAW
    app.run()
    app.number_input(key="filter_min_rate").set_value(150.0).run()
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        app.button(key="apply_filters").click().run()
    assert not app.exception and len(app.session_state["orders"]) == 2
    return app


@pytest.mark.parametrize("uploaded", [False, True])
def test_new_sessions_restore_text_all_parsed_orders_and_applied_filters_without_model(uploaded):
    original = parse_batch(uploaded=uploaded)
    record_id = original.session_state["current_import_id"]
    events_before = len(history_store.get_import(record_id)["events"])
    with patch("tutor_screen.pipeline.process_batch") as model:
        for _ in range(2):
            reopened = AppTest.from_file(APP, default_timeout=15).run()
            assert not reopened.exception and reopened.title[0].value == "订单导入"
            assert reopened.text_area(key="order_text").value == RAW
            assert reopened.session_state["uploaded_order_file"] is None
            assert len(reopened.session_state["orders"]) == 2
            assert [order.order_number for order in reopened.session_state["filtered"]] == ["SAVE102"]
            assert reopened.number_input(key="filter_min_rate").value == 150
            assert reopened.session_state["current_import_id"] == record_id
        assert len(history_store.get_import(record_id)["events"]) == events_before
        reopened.button(key="apply_filters").click().run()
        assert not reopened.exception and reopened.title[0].value == "筛选结果"
        model.assert_not_called()
    assert history_store.count_imports() == 1
    assert "SYNTHETIC-KEY" not in batch_store.batch_path().read_text(encoding="utf-8")


@pytest.mark.parametrize("fails", [False, True])
def test_failed_or_empty_replacement_and_unapplied_edits_keep_previous_saved_batch(fails):
    app = parse_batch()
    previous = batch_store.batch_path().read_bytes()
    app.button(key="edit_filters").click().run()
    app.text_area(key="order_text").set_value("订单号 SAVE999 初中物理 300/h 线上").run()
    app.button(key="apply_filters").click().run()
    with patch("tutor_screen.pipeline.process_batch", side_effect=RuntimeError("failed") if fails else None, return_value=[]):
        next(button for button in app.button if button.label == "确认覆盖并处理").click().run()
    assert not app.exception and batch_store.batch_path().read_bytes() == previous
    reopened = AppTest.from_file(APP).run()
    assert not reopened.exception and reopened.text_area(key="order_text").value == RAW
    assert len(reopened.session_state["orders"]) == 2


def test_unapplied_new_text_does_not_replace_persisted_parsed_input_on_reset():
    app = parse_batch()
    app.button(key="edit_filters").click().run()
    app.text_area(key="order_text").set_value("未解析的新草稿").run()
    app.button(key="reset_filters").click().run()
    reopened = AppTest.from_file(APP).run()
    assert not reopened.exception and reopened.text_area(key="order_text").value == RAW
    assert len(reopened.session_state["filtered"]) == 2


def test_restore_keeps_full_batch_when_saved_filters_exclude_every_order():
    app = parse_batch()
    app.button(key="edit_filters").click().run()
    app.number_input(key="filter_min_rate").set_value(250.0).run()
    app.button(key="apply_filters").click().run()
    reopened = AppTest.from_file(APP).run()
    assert not reopened.exception and reopened.text_area(key="order_text").value == RAW
    assert len(reopened.session_state["orders"]) == 2 and reopened.session_state["filtered"] == []
    reopened.button(key="reset_filters").click().run()
    assert not reopened.exception and len(reopened.session_state["filtered"]) == 2


def test_windows_txt_line_endings_do_not_trigger_reparse_after_textarea_restore():
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["api_key"] = "SYNTHETIC-KEY"
    windows_text = RAW.replace("\n", "\r\n")
    app.session_state["uploaded_order_file"] = {"name": "windows.txt", "content": windows_text.encode("utf-8-sig")}
    app.run()
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        app.button(key="apply_filters").click().run()
    assert not app.exception and batch_store.load_batch().input_text == windows_text
    reopened = AppTest.from_file(APP).run()
    with patch("tutor_screen.pipeline.process_batch") as model:
        reopened.text_area(key="order_text").set_value(RAW).run()
        reopened.button(key="apply_filters").click().run()
        model.assert_not_called()
    assert not reopened.exception and reopened.session_state["page"] == "筛选结果"
    assert history_store.count_imports() == 1
    assert batch_store.load_batch().input_text == windows_text


def test_clear_logs_preserves_saved_batch_across_new_sessions_without_recreating_logs():
    app = parse_batch()
    app.radio(key="nav_widget").set_value("开发日志").run()
    app.button(key="clear_history").click().run()
    app.button(key="confirm_clear_history").click().run()
    with patch("tutor_screen.pipeline.process_batch") as model:
        reopened = AppTest.from_file(APP).run()
        assert not reopened.exception and reopened.text_area(key="order_text").value == RAW
        assert reopened.session_state["current_import_id"] is None
        reopened.button(key="apply_filters").click().run()
        assert not reopened.exception and len(reopened.session_state["orders"]) == 2
        assert history_store.count_imports() == 0
        model.assert_not_called()


def test_restore_old_logged_batch_without_reparsing_or_duplicate_log():
    value = Order(**parse_local(RAW).model_dump(), order_id="LEGACY", raw_text=RAW)
    record_id = history_store.create_import(source_type="TXT", input_text=RAW, filename="old.txt")
    history_store.append_event(record_id, kind="解析完成", parsed_orders=[value])
    with patch("tutor_screen.pipeline.process_batch") as model:
        app = AppTest.from_file(APP).run()
        assert not app.exception and app.text_area(key="order_text").value == RAW
        assert [order.order_number for order in app.session_state["orders"]] == ["SAVE101", "SAVE102"]
        assert app.session_state["orders"][0].order_id == "LEGACY"
        assert len(history_store.get_import(record_id)["events"]) == 1
        assert batch_store.batch_path().exists() and history_store.count_imports() == 1
        model.assert_not_called()


def test_corrupt_batch_shows_warning_and_keeps_original_file():
    path = batch_store.batch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"invalid PRIVATE")
    app = AppTest.from_file(APP).run()
    assert not app.exception and not app.text_area(key="order_text").value
    assert any("本地订单恢复失败" in item.value for item in app.warning)
    assert path.read_bytes() == b"invalid PRIVATE"


def test_history_failure_does_not_block_independent_batch_restore():
    parse_batch()
    with patch("tutor_screen.history_store.has_import", side_effect=history_store.HistoryStoreError("模拟日志故障")):
        app = AppTest.from_file(APP).run()
    assert not app.exception and app.text_area(key="order_text").value == RAW
    assert len(app.session_state["orders"]) == 2


def test_save_failure_keeps_parsed_orders_and_shows_warning():
    with patch("tutor_screen.batch_store.save_batch", side_effect=batch_store.BatchStoreError("本地订单保存失败")):
        app = parse_batch()
    assert len(app.session_state["orders"]) == 2
    assert any("本地订单保存失败" in item.value for item in app.warning)
