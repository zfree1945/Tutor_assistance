"""地图设置完整操作链和本机持久化，所有地点均为合成数据。"""
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from streamlit.proto.TextInput_pb2 import TextInput

from tutor_screen import map_store
from tutor_screen.maps import GeoPoint, MapCredentials


APP = Path(__file__).resolve().parents[2] / "app.py"


def setup_app():
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["page"] = "地图设置"
    app.run()
    assert not app.exception
    return app


def test_settings_groups_credentials_origins_and_cache_without_network():
    with patch("tutor_screen.maps.AmapClient._request", side_effect=AssertionError("No implicit requests")):
        app = setup_app()
    credentials = app.container(key="map_credentials")
    origins = app.container(key="map_origins")
    cache = app.container(key="map_cache")
    assert [heading.value for heading in app.subheader] == ["地图与查询凭据", "常用出发点", "本机缓存"]
    assert len(credentials.columns) == 4
    assert credentials.columns[0].text_input[0].key == "map_js_key"
    assert credentials.columns[1].text_input[0].key == "map_security_code"
    assert credentials.columns[2].text_input[0].key == "map_web_key"
    assert credentials.columns[3].text_input[0].key == "map_default_city"
    assert len(origins.columns) == 2
    assert origins.button(key="map_origin_search").disabled
    assert cache.button(key="map_clear_cache").label == "清除接口定位与通勤缓存"
    assert any("两个 Key" in item.value for item in credentials.caption)
    for key in ["map_js_key", "map_security_code", "map_web_key"]:
        assert credentials.text_input(key=key).proto.type == TextInput.PASSWORD


def test_origin_and_sort_controls_are_visible_and_remain_drafts_until_apply():
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["page"] = "订单导入"
    app.run()
    assert not app.exception
    controls = app.container(key="map_filter_controls")
    assert len(controls.columns) == 2
    assert not any(item.label == "位置与距离筛选" for item in app.expander)
    assert not controls.checkbox and not controls.number_input
    app.radio(key="nav_widget").set_value("订单导入").run()
    assert app.selectbox(key="map_sort_draft").value == "距离由近到远"
    app.selectbox(key="map_sort_draft").set_value("原始顺序").run()
    assert app.session_state["map_applied"]["sort"] == "距离由近到远"
    app.button(key="apply_filters").click().run()
    assert not app.exception
    assert "hide_unknown" not in app.session_state["map_applied"]
    assert app.session_state["map_applied"]["sort"] == "原始顺序"


def test_save_encrypted_settings_without_implicit_network_or_cache_permission():
    app = setup_app()
    with patch("tutor_screen.maps.AmapClient._request", side_effect=AssertionError("No implicit requests")):
        for key, value in {"map_js_key": "SYNTHETIC-SETUP-JS", "map_security_code": "SYNTHETIC-SETUP-CODE", "map_web_key": "SYNTHETIC-SETUP-PRIVATE", "map_default_city": "温州市"}.items():
            app.text_input(key=key).set_value(value)
        app.button(key="map_save_settings").click().run()
    assert not app.exception
    settings = map_store.load_settings()
    assert settings.credentials.web_key == "SYNTHETIC-SETUP-PRIVATE"
    assert settings.default_city == "温州市" and not settings.persist_api_cache
    assert b"SYNTHETIC-SETUP-PRIVATE" not in map_store.MAP_PATH.read_bytes()
    reopened = setup_app()
    assert reopened.text_input(key="map_web_key").value == "SYNTHETIC-SETUP-PRIVATE"


def test_search_choose_save_origin_and_activate_from_results():
    map_store.save_settings(map_store.MapSettings(default_city="温州", credentials=MapCredentials(web_key="SYNTHETIC-ORIGIN")))
    app = setup_app()
    app.text_input(key="map_origin_name").set_value("学校")
    app.text_input(key="map_origin_query").set_value("合成学校")
    location = GeoPoint(lng=120.7, lat=28.0, label="合成学校", city="温州")
    with patch("tutor_screen.maps.AmapClient.search", return_value=[location]) as search:
        app.button(key="map_origin_search").click().run()
    search.assert_called_once_with("合成学校", "温州")
    app.button(key="map_origin_save").click().run()
    assert not app.exception
    saved = map_store.list_origins()
    assert len(saved) == 1 and saved[0].name == "学校"
    app.radio(key="nav_widget").set_value("筛选结果").run()
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.selectbox(key="map_origin_draft").set_value(saved[0].id).run()
    assert app.session_state["map_applied"]["origin"] is None
    app.button(key="apply_filters").click().run()
    assert app.session_state["map_applied"]["origin_id"] == saved[0].id
    assert map_store.load_settings().active_origin_id == saved[0].id


def test_business_filters_apply_without_origin_after_removing_distance_controls():
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["page"] = "筛选结果"
    app.run()
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.number_input(key="filter_min_rate").set_value(200.0).run()
    app.button(key="apply_filters").click().run()
    assert not app.exception
    assert app.session_state["criteria"].min_hourly_rate == 200
    assert not any("出发点" in warning.value for warning in app.warning)


def test_delete_active_origin_clears_geometry_and_cache_clear_preserves_manual():
    point = GeoPoint(lng=120.7, lat=28.0, city="温州", source="user")
    origin = map_store.save_origin("住处", point)
    map_store.save_settings(map_store.MapSettings(default_city="温州", active_origin_id=origin.id))
    map_store.save_manual("合成小区", "温州", point)
    app = setup_app()
    app.button(key="map_clear_cache").click().run()
    assert map_store.get_manual("合成小区", "温州") == point
    assert map_store.list_origins() == [origin]
    app.button(key=f"map_delete_origin_{origin.id}").click().run()
    assert not app.exception and map_store.list_origins() == []
    assert app.session_state["map_applied"]["origin"] is None
    assert app.session_state["_map_results_dirty"]
