"""已有会话只升级一次、新窗口展示、保存以及每周次数筛选。"""
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from tutor_screen import history_store as store
from tutor_screen.models import Order
from tutor_screen.local_parser import parse_local
from wz_schedule_samples import EXPECTED, ORDERS, RAW

APP = Path(__file__).parents[2] / "app.py"


def test_legacy_session_is_updated_once_using_applied_conditions_and_preserving_old_snapshot():
    old = Order(order_id="ORD-0001", raw_text=ORDERS[0], grade="小学", subjects=["英语"],
                lesson_hours=None, days=[7], weekly_sessions=1, mode="线下",
                address="平阳县万全新悦广场\n详情:女老师", uncertain_fields=["每次时长"], extraction_source="model")
    record_id = store.create_import(source_type="粘贴", input_text=ORDERS[0])
    store.append_event(record_id, kind="解析完成", orders=[old])
    app = AppTest.from_file(APP)
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [old]
    app.session_state["filtered"] = [old]
    app.session_state["current_import_id"] = record_id
    app.session_state["filter_min_rate"] = 999.0  # 未应用的草稿不会自动改变接单决定。
    with patch("tutor_screen.llm_client._client") as model:
        app.run()
        model.assert_not_called()
    assert not app.exception and len(app.session_state["filtered"]) == 1
    assert app.session_state["filtered"][0].lesson_hours == 2
    assert app.session_state["filtered"][0].address == EXPECTED[0]["address"]
    assert any("上课时间：周日 13:00–15:00" in caption.value for caption in app.caption)
    events = store.get_import(record_id)["events"]
    assert len(events) == 2 and events[-1]["kind"] == "字段规则升级"
    assert events[0]["orders"][0]["lesson_hours"] is None
    assert events[-1]["orders"][0]["lesson_hours"] == 2
    app.run()
    assert len(store.get_import(record_id)["events"]) == 2


def test_windows_frequency_unknown_duration_and_persistent_snapshots_are_consistent():
    app = AppTest.from_file(APP)
    app.session_state["api_key"] = "dummy-wz-ui-key"
    app.run()
    app.text_area(key="order_text").set_value(RAW).run()
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        app.button(key="apply_filters").click().run()
    assert not app.exception and len(app.session_state["orders"]) == 4
    captions = "\n".join(value.value for value in app.caption)
    assert "上课时间：周日 13:00–15:00" in captions
    assert "上课时间：周二、周三、周四、周五 14:00–20:30" in captions
    assert "上课时间：周六、周日 09:00–17:00" in captions
    assert "每周 6 次" in captions
    record_id = app.session_state["current_import_id"]
    record = store.get_import(record_id)
    assert len(record["events"][-1]["orders"][3]["schedule_windows"]) == 2
    assert "dummy-wz-ui-key" not in str(record)
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.selectbox(key="filter_frequency").set_value(5).run()
    app.button(key="apply_filters").click().run()
    assert not app.exception and len(app.session_state["filtered"]) == 3
    assert store.get_import(record_id)["events"][-1]["orders"][3]["match_status"] == "已排除"
    reopened = AppTest.from_file(APP).run()
    reopened.radio(key="nav_widget").set_value("开发日志").run()
    assert not reopened.exception and reopened.metric[0].value == "1"
    reopened.selectbox(key="history_order_selector").set_value(3).run()
    assert not reopened.exception and any("次数" in value.value for value in reopened.markdown)
