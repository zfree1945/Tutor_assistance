"""UI/IMP/CFG/EXP：隔离AppTest，验证实际用户操作顺序。"""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen.config_store import ConfigStoreError, ModelConfig, save_config
from tutor_screen import config_store
from tutor_screen.local_parser import parse_local
from helpers import order

APP = Path(__file__).resolve().parents[2] / "app.py"
RAW = "订单号 A100 小学数学 150/h 线上 周六晚上 每周1次"


def app_with_batch():
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["api_key"] = "dummy-key"
    app.session_state["orders"] = [order(RAW)]
    app.session_state["filtered"] = [order(RAW)]
    return app.run()


def click_label(app, label):
    return next(b for b in app.button if b.label == label).click().run()


@pytest.mark.parametrize("raw,uploaded", [("", None), (" \n\t ", None), ("", b""), ("", b" \r\n"), ("", b"\xef\xbb\xbf"), ("小学数学", b"\xef\xbb\xbf"), ("", b"\xff\xfe")])
def test_empty_upload_encoding_and_bom(raw, uploaded):
    app = AppTest.from_file(APP)
    app.session_state["api_key"] = "dummy-key"
    if uploaded is not None:
        app.session_state["uploaded_order_file"] = {"name": "empty.txt", "content": uploaded}
    app.run()
    app.text_area(key="order_text").set_value(raw)
    with patch("tutor_screen.pipeline.process_batch", return_value=[]) as process:
        app.button(key="apply_filters").click().run()
    assert not app.exception
    if raw.strip():
        process.assert_called_once()
        assert process.call_args.args[0] == raw
    else:
        process.assert_not_called()
        assert bool(app.error) == (uploaded is not None)


def test_replace_cancel_confirm_and_failed_limit_keep_previous_batch():
    app = app_with_batch()
    app.text_area(key="order_text").set_value("订单号 A101 初中英语 200/h 线上").run()
    with patch("tutor_screen.pipeline.process_batch", return_value=[order("订单号 A101 初中英语")]) as process:
        app.button(key="apply_filters").click().run()
        process.assert_not_called()
        click_label(app, "取消覆盖")
        assert app.session_state["orders"][0].raw_text == RAW
        app.button(key="apply_filters").click().run()
        click_label(app, "确认覆盖并处理")
        process.assert_called_once()
        assert app.session_state["orders"][0].raw_text == "订单号 A101 初中英语"
    app.radio(key="nav_widget").set_value("订单导入").run()
    previous = app.session_state["orders"]
    too_many = "\n".join(f"订单号 A{i:03d} 小学数学" for i in range(301))
    app.text_area(key="order_text").set_value(too_many).run()
    app.button(key="apply_filters").click().run()
    click_label(app, "确认覆盖并处理")
    assert not app.exception and app.error
    assert app.session_state["orders"] == previous


def test_clear_buttons_are_removed_and_navigation_preserves_orders_and_key():
    app = app_with_batch()
    app.text_area(key="order_text").set_value(RAW).run()
    assert not {"clear_orders", "confirm_clear", "start_processing"}.intersection(item.key for item in app.button)
    app.radio(key="nav_widget").set_value("筛选结果").run()
    assert not app.exception
    assert app.session_state["orders"] and app.session_state["filtered"]
    assert app.session_state["api_key"] == "dummy-key"


def test_filter_draft_apply_reset_never_calls_model():
    app = app_with_batch()
    app.radio(key="nav_widget").set_value("筛选结果").run()
    with patch("tutor_screen.pipeline.extract_order") as model:
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.number_input(key="filter_min_rate").set_value(151).run()
        assert len(app.session_state["filtered"]) == 1
        app.button(key="apply_filters").click().run()
        assert app.session_state["filtered"] == []
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.button(key="reset_filters").click().run()
        assert len(app.session_state["filtered"]) == 1
        model.assert_not_called()
    assert not app.exception


def test_corrupt_config_is_visible_and_not_overwritten():
    config_store.CONFIG_PATH.write_bytes(b"bad-dummy-key")
    app = AppTest.from_file(APP).run()
    assert not app.exception and app.warning
    app.radio(key="nav_widget").set_value("模型设置").run()
    assert app.error
    app.text_input(key="api_key").set_value("dummy-new-key").run()
    assert app.error and config_store.CONFIG_PATH.read_bytes() == b"bad-dummy-key"


def test_save_failure_never_claims_saved():
    app = AppTest.from_file(APP).run()
    app.radio(key="nav_widget").set_value("模型设置").run()
    with patch("tutor_screen.config_store.save_config", side_effect=ConfigStoreError("测试保存失败")):
        app.text_input(key="api_key").set_value("dummy-key").run()
    assert not app.exception and app.error
    assert not any("已自动保存" in c.value for c in app.caption)


def test_unknown_saved_provider_does_not_crash_settings():
    save_config(ModelConfig(provider="removed-provider", api_key="dummy-key", base_url="https://example.test", model_name="model"))
    app = AppTest.from_file(APP).run()
    app.radio(key="nav_widget").set_value("模型设置").run()
    assert not app.exception
    assert app.selectbox(key="provider").value in app.selectbox(key="provider").options


def test_export_button_clearly_states_filtered_scope():
    app = app_with_batch()
    app.radio(key="nav_widget").set_value("筛选结果").run()
    assert not app.exception
    assert any(node.proto.label == "导出当前筛选 CSV" for node in app.get("download_button"))
