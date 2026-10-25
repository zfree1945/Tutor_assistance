from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[2] / "app.py"
ORDER_TEXT = "订单号 A100 小学数学 150元/小时 线上 周一晚上 每周1次"


def create_app() -> AppTest:
    app = AppTest.from_file(APP_PATH).run()
    assert not app.exception
    return app


def navigate(app: AppTest, page: str) -> None:
    app.radio(key="nav_widget").set_value(page).run()
    assert not app.exception
    assert app.session_state["page"] == page
    assert app.radio(key="nav_widget").value == page


def configure_model(app: AppTest) -> None:
    navigate(app, "模型设置")
    assert app.selectbox(key="provider").value == "DeepSeek"
    assert app.selectbox(key="provider").disabled
    app.text_input(key="api_key").set_value("dummy-test-key").run()
    app.text_input(key="base_url").set_value("https://example.test/v1").run()
    app.text_input(key="model_name").set_value("test-model").run()
    assert not app.exception


def test_configured_key_survives_successful_connection_and_navigation():
    app = create_app()
    assert app.radio(key="nav_widget").options == ["模型设置", "地图设置", "订单导入", "筛选结果", "开发日志"]
    configure_model(app)
    with patch("tutor_screen.llm_client.test_connection") as connection:
        app.button(key="test_connection").click().run()
    connection.assert_called_once_with("dummy-test-key", "https://example.test/v1", "test-model")
    assert app.success
    for page in ["订单导入", "筛选结果", "模型设置", "订单导入", "模型设置"]:
        navigate(app, page)
        assert app.session_state["api_key"] == "dummy-test-key"
        assert app.session_state["provider"] == "DeepSeek"
        assert app.session_state["base_url"] == "https://example.test/v1"
        assert app.session_state["model_name"] == "test-model"
        assert not any("尚未配置 API Key" in info.value for info in app.info)
    assert app.text_input(key="api_key").value == "dummy-test-key"


def test_sidebar_navigation_preserves_order_text_without_step_cards():
    app = create_app()
    app.text_area(key="order_text").set_value(ORDER_TEXT).run()
    assert not {"step_settings", "step_import", "step_results", "clear_orders", "start_processing"}.intersection(item.key for item in app.button)
    navigate(app, "模型设置")
    assert not app.exception
    assert app.title[0].value == "模型设置"
    navigate(app, "订单导入")
    assert app.text_area(key="order_text").value == ORDER_TEXT
    navigate(app, "订单导入")
    assert app.title[0].value == "订单导入"
    assert app.text_area(key="order_text").value == ORDER_TEXT
    navigate(app, "筛选结果")
    assert not app.exception
    assert app.title[0].value == "筛选结果"
    assert app.radio(key="nav_widget").value == "筛选结果"
    navigate(app, "订单导入")
    assert app.text_area(key="order_text").value == ORDER_TEXT


def test_unapplied_filter_draft_survives_navigation_and_reset():
    app = create_app()
    navigate(app, "订单导入")
    app.selectbox(key="filter_gender").set_value("女").run()
    app.multiselect(key="filter_grades").set_value(["小学"]).run()
    app.multiselect(key="filter_subjects").set_value(["数学", "英语"]).run()
    app.selectbox(key="filter_mode").set_value("线上").run()
    app.multiselect(key="filter_days").set_value(["周一", "周三"]).run()
    app.selectbox(key="filter_frequency").set_value(2).run()
    app.multiselect(key="filter_slots").set_value(["晚上"]).run()
    app.number_input(key="filter_min_rate").set_value(150.0).run()
    assert app.session_state["criteria"].min_hourly_rate is None
    navigate(app, "模型设置")
    navigate(app, "筛选结果")
    navigate(app, "订单导入")
    assert app.selectbox(key="filter_gender").value == "女"
    assert app.multiselect(key="filter_grades").value == ["小学"]
    assert app.multiselect(key="filter_subjects").value == ["数学", "英语"]
    assert app.selectbox(key="filter_mode").value == "线上"
    assert app.multiselect(key="filter_days").value == ["周一", "周三"]
    assert app.selectbox(key="filter_frequency").value == 2
    assert app.multiselect(key="filter_slots").value == ["晚上"]
    assert app.number_input(key="filter_min_rate").value == 150.0
    app.button(key="apply_filters").click().run()
    assert not app.exception
    assert app.session_state["criteria"].min_hourly_rate == 150
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.button(key="reset_filters").click().run()
    assert not app.exception
    assert app.multiselect(key="filter_grades").value == []
    assert app.number_input(key="filter_min_rate").value == 0
    navigate(app, "筛选结果")
    navigate(app, "订单导入")
    assert app.number_input(key="filter_min_rate").value == 0


def test_saved_upload_can_be_used_after_navigation_and_removed():
    app = create_app()
    # AppTest 不模拟浏览器文件选择；直接种入上传回调保存的会话数据。
    app.session_state["uploaded_order_file"] = {"name": "orders.txt", "content": ORDER_TEXT.encode("utf-8")}
    configure_model(app)
    navigate(app, "订单导入")
    assert any("orders.txt" in caption.value for caption in app.caption)
    with patch("tutor_screen.pipeline.process_batch", return_value=[]) as process:
        app.button(key="apply_filters").click().run()
    process.assert_called_once()
    assert process.call_args.args[0] == ORDER_TEXT
    assert not app.exception
    navigate(app, "订单导入")
    app.text_area(key="order_text").set_value(ORDER_TEXT).run()
    app.button(key="apply_filters").click().run()
    assert any("粘贴文本和 TXT 文件都有内容" in error.value for error in app.error)
    app.button(key="remove_upload").click().run()
    assert not app.exception
    assert app.session_state["uploaded_order_file"] is None
    assert app.text_area(key="order_text").value == ORDER_TEXT


def test_removed_clear_action_preserves_inputs_results_and_configuration():
    app = create_app()
    configure_model(app)
    navigate(app, "订单导入")
    app.number_input(key="filter_min_rate").set_value(180.0).run()
    app.button(key="apply_filters").click().run()
    navigate(app, "订单导入")
    app.text_area(key="order_text").set_value(ORDER_TEXT).run()
    app.session_state["uploaded_order_file"] = {"name": "orders.txt", "content": b"data"}
    assert not {"clear_orders", "confirm_clear"}.intersection(item.key for item in app.button)
    navigate(app, "筛选结果")
    navigate(app, "订单导入")
    assert not app.exception
    assert app.text_area(key="order_text").value == ORDER_TEXT
    assert app.session_state["uploaded_order_file"] == {"name": "orders.txt", "content": b"data"}
    assert app.session_state["api_key"] == "dummy-test-key"
    assert app.session_state["criteria"].min_hourly_rate == 180
