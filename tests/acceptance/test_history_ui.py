"""订单页精简展示、日志持久化以及清空的作用范围。"""
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from tutor_screen import history_store as store
from tutor_screen.local_parser import parse_local
from tutor_screen.models import Order

APP = Path(__file__).parents[2] / "app.py"
RAW = "订单号 A100 小学数学 150/h 线上 周六晚上 每周1次"


def make_order(raw=RAW, **updates):
    values = parse_local(raw).model_dump()
    values.update(updates)
    return Order(**values, order_id="ORD-0001", raw_text=raw, extraction_source="model")


def create_app():
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["api_key"] = "dummy-private-config-key"
    app.run()
    assert not app.exception
    return app


def import_batch(app):
    app.text_area(key="order_text").set_value(RAW).run()
    with patch("tutor_screen.pipeline.process_batch", return_value=[make_order()]):
        app.button(key="apply_filters").click().run()
    assert not app.exception and app.session_state["page"] == "筛选结果"
    return app.session_state["current_import_id"]


def test_result_card_keeps_raw_and_chinese_uncertainty_without_debug_fields():
    app = AppTest.from_file(APP)
    unknown_raw = "订单号 A100 小学全科 150/h 线上 每周1次，时间可协商，每次时长待定"
    value = make_order(unknown_raw, uncertain_fields=["lesson_hours", "每次时长", "time_slots", "星期", "科目"])
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [value]
    app.session_state["filtered"] = [value]
    app.run()
    assert not app.exception and any(text.value == unknown_raw for text in app.text)
    assert not app.json
    displayed = "\n".join(element.value for element in app.markdown)
    assert "结构化字段" not in displayed and "字段来源" not in displayed and "本地匹配理由" not in displayed and "提取方式" not in displayed
    uncertainty = next(element.value for element in app.markdown if "待确认条件：" in element.value)
    assert "lesson_hours" not in uncertainty and uncertainty.count("每次时长") == 1
    assert "科目" in uncertainty and "星期" in uncertainty


def test_parse_filter_reset_append_snapshots_but_navigation_does_not():
    app = create_app()
    record_id = import_batch(app)
    assert store.count_imports() == 1
    assert [event["kind"] for event in store.get_import(record_id)["events"]] == ["开始解析", "解析完成"]
    for page in ["开发日志", "模型设置", "订单导入", "筛选结果"]:
        app.radio(key="nav_widget").set_value(page).run()
        assert not app.exception
    assert len(store.get_import(record_id)["events"]) == 2
    with patch("tutor_screen.llm_client._client") as model:
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.number_input(key="filter_min_rate").set_value(200.0).run()
        assert len(store.get_import(record_id)["events"]) == 2
        app.button(key="apply_filters").click().run()
        assert app.session_state["filtered"] == []
        event = store.get_import(record_id)["events"][-1]
        assert event["kind"] == "应用筛选" and event["orders"][0]["match_status"] == "已排除"
        assert event["orders"][0]["match_reason"] and event["orders"][0]["field_sources"]
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.button(key="reset_filters").click().run()
        model.assert_not_called()
    assert store.get_import(record_id)["events"][-1]["kind"] == "重置筛选"
    assert len(app.session_state["filtered"]) == 1


def test_logs_survive_new_session_and_show_debug_only_on_log_page():
    app = create_app()
    record_id = import_batch(app)
    reopened = AppTest.from_file(APP).run()
    reopened.radio(key="nav_widget").set_value("开发日志").run()
    assert not reopened.exception
    assert reopened.metric[0].value == "1"
    displayed = "\n".join(element.value for element in reopened.markdown)
    assert "结构化字段" in displayed and "字段来源" in displayed and "本地匹配理由" in displayed and "提取方式" in displayed
    assert any(text.value == RAW for text in reopened.text)
    assert "dummy-private-config-key" not in str(store.get_import(record_id))


def test_clear_cancel_then_confirm_preserves_current_orders_key_and_filters():
    app = create_app()
    record_id = import_batch(app)
    app.radio(key="nav_widget").set_value("开发日志").run()
    app.button(key="clear_history").click().run()
    assert store.get_import(record_id) is not None
    app.button(key="cancel_clear_history").click().run()
    assert store.count_imports() == 1
    current = app.session_state["orders"]
    criteria = app.session_state["criteria"]
    app.button(key="clear_history").click().run()
    app.button(key="confirm_clear_history").click().run()
    assert not app.exception and store.count_imports() == 0 and app.success
    assert app.session_state["orders"] == current and app.session_state["criteria"] == criteria
    assert app.session_state["api_key"] == "dummy-private-config-key"
    app.radio(key="nav_widget").set_value("筛选结果").run()
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.button(key="apply_filters").click().run()
    assert store.count_imports() == 0


def test_import_page_has_no_clear_orders_action_and_keeps_history():
    app = create_app()
    record_id = import_batch(app)
    app.radio(key="nav_widget").set_value("订单导入").run()
    assert not {"clear_orders", "confirm_clear"}.intersection(item.key for item in app.button)
    assert not app.exception and len(app.session_state["orders"]) == 1
    assert store.get_import(record_id) is not None


def test_log_failure_is_visible_but_does_not_lose_processed_orders():
    app = create_app()
    with patch("tutor_screen.history_store.create_import", side_effect=store.HistoryStoreError("模拟日志不可写")):
        record_id = import_batch(app)
    assert record_id is None and len(app.session_state["orders"]) == 1
    assert any("日志" in warning.value and "不可写" in warning.value for warning in app.warning)
    assert not app.exception


def test_uploaded_record_is_reused_for_parsing():
    app = create_app()
    content = RAW.encode("utf-8-sig")
    record_id = store.create_import(source_type="TXT", input_text=RAW, filename="orders.txt", file_content=content)
    app.session_state["uploaded_order_file"] = {"name": "orders.txt", "content": content, "history_id": record_id}
    app.run()
    with patch("tutor_screen.pipeline.process_batch", return_value=[make_order()]):
        app.button(key="apply_filters").click().run()
    assert not app.exception and store.count_imports() == 1
    assert app.session_state["current_import_id"] == record_id
    assert store.get_import(record_id)["file_content"] == content


def test_unexpected_batch_failure_is_logged_without_exception_secrets():
    app = create_app()
    app.text_area(key="order_text").set_value(RAW).run()
    with patch("tutor_screen.pipeline.process_batch", side_effect=RuntimeError("dummy-secret-error")):
        app.button(key="apply_filters").click().run()
    assert not app.exception and app.error
    record = store.get_import(store.list_imports()[0]["id"])
    assert record["status"] == "未完成" and "dummy-secret-error" not in str(record)
    assert app.text_area(key="order_text").value == RAW


def test_legacy_session_is_backfilled_once_and_not_resurrected_after_clear():
    app = AppTest.from_file(APP)
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [make_order()]
    app.session_state["filtered"] = [make_order()]
    app.run()
    assert not app.exception and store.count_imports() == 1
    record = store.get_import(app.session_state["current_import_id"])
    assert record["source_type"] == "会话补存" and record["filename"] is None
    app.run()
    assert store.count_imports() == 1 and len(store.get_import(record["id"])["events"]) == 1
    app.radio(key="nav_widget").set_value("开发日志").run()
    app.button(key="clear_history").click().run()
    app.button(key="confirm_clear_history").click().run()
    app.radio(key="nav_widget").set_value("筛选结果").run()
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.button(key="apply_filters").click().run()
    assert not app.exception and store.count_imports() == 0


def test_unparseable_order_still_shows_unknown_business_conditions():
    app = AppTest.from_file(APP)
    value = Order(order_id="ORD-0001", raw_text="陌生订单格式", extraction_source="model")
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [value]
    app.session_state["filtered"] = [value]
    app.run()
    assert not app.exception
    displayed = next(element.value for element in app.markdown if "待确认条件：" in element.value)
    assert all(name in displayed for name in ["年级", "科目", "时薪", "上课方式", "星期"])
