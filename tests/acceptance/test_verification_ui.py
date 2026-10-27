"""QA-12/15/16/17: independent sessions and failure/retry workflows."""
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from tutor_screen import history_store as store
from test_history_ui import APP, RAW, create_app, import_batch, make_order


def button_by_label(app,label):
    return next(button for button in app.button if button.label == label)


def test_qa12_other_session_clear_then_filter_and_upgrade_do_not_restore_deleted_log():
    first = create_app()
    record_id = import_batch(first)
    second = AppTest.from_file(APP,default_timeout=15).run()
    second.radio(key="nav_widget").set_value("开发日志").run()
    second.button(key="clear_history").click().run()
    second.button(key="confirm_clear_history").click().run()
    assert not second.exception and store.count_imports() == 0
    first.radio(key="nav_widget").set_value("订单导入").run()
    first.button(key="apply_filters").click().run()
    assert not first.exception and len(first.session_state["orders"]) == 1
    assert store.get_import(record_id) is None and store.count_imports() == 0
    first.session_state["_local_fields_revision"] = "old-rules"
    first.radio(key="nav_widget").set_value("筛选结果").run()
    assert not first.exception and store.count_imports() == 0


def test_qa16_replace_cancel_failure_retry_keeps_previous_batch():
    app = create_app()
    previous_id = import_batch(app)
    previous_orders = [o.model_dump() for o in app.session_state["orders"]]
    app.radio(key="nav_widget").set_value("订单导入").run()
    replacement = RAW.replace("A100","QA900").replace("小学数学","初中英语")
    app.text_area(key="order_text").set_value(replacement).run()
    app.button(key="apply_filters").click().run()
    button_by_label(app,"取消覆盖").click().run()
    assert [o.model_dump() for o in app.session_state["orders"]] == previous_orders
    assert store.count_imports() == 1
    app.button(key="apply_filters").click().run()
    with patch("tutor_screen.pipeline.process_batch",side_effect=RuntimeError("SYNTHETIC-ERROR-SECRET")):
        button_by_label(app,"确认覆盖并处理").click().run()
    assert not app.exception and app.error
    assert [o.model_dump() for o in app.session_state["orders"]] == previous_orders
    assert app.session_state["current_import_id"] == previous_id
    failed = next(store.get_import(r["id"]) for r in store.list_imports() if r["id"] != previous_id)
    assert failed["status"] == "未完成" and "SYNTHETIC-ERROR-SECRET" not in str(failed)
    app.button(key="apply_filters").click().run()
    with patch("tutor_screen.pipeline.process_batch",return_value=[make_order(replacement)]):
        button_by_label(app,"确认覆盖并处理").click().run()
    assert not app.exception and app.session_state["orders"][0].raw_text == replacement
    assert store.count_imports() == 3
    assert store.get_import(previous_id)["status"] == "已解析"


def test_qa16_two_input_sources_rejected_without_changing_previous_orders():
    app = create_app()
    previous = import_batch(app)
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.session_state["uploaded_order_file"] = {"name":"synthetic.txt","content":RAW.encode()}
    app.run()
    with patch("tutor_screen.pipeline.process_batch") as process:
        app.button(key="apply_filters").click().run()
        process.assert_not_called()
    assert not app.exception and any("都有内容" in e.value for e in app.error)
    assert app.session_state["current_import_id"] == previous
    assert len(app.session_state["orders"]) == 1 and store.count_imports() == 1


def test_qa15_rule_upgrade_uses_applied_filters_after_draft_change():
    app = create_app()
    record_id = import_batch(app)
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.number_input(key="filter_min_rate").set_value(151.0).run()
    app.button(key="apply_filters").click().run()
    assert app.session_state["filtered"] == []
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.number_input(key="filter_min_rate").set_value(0.0).run()
    # Use the same state key that the actual app reads for upgrade detection.
    app.session_state["_local_fields_revision"] = "old"
    before = len(store.get_import(record_id)["events"])
    app.radio(key="nav_widget").set_value("筛选结果").run()
    assert not app.exception and app.session_state["filtered"] == []
    assert len(store.get_import(record_id)["events"]) == before + 1
    assert store.get_import(record_id)["events"][-1]["kind"] == "字段规则升级"
    app.run()
    assert len(store.get_import(record_id)["events"]) == before + 1
