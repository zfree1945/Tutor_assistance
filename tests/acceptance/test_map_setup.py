"""地图设置完整操作链和本机持久化，所有地点均为合成数据。"""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.proto.TextInput_pb2 import TextInput

from tutor_screen import map_store
from tutor_screen.maps import GeoPoint, MapCredentials, MapAPIError


APP = Path(__file__).resolve().parents[2] / "app.py"


def setup_app():
    app = AppTest.from_string(
        "import runpy\nimport streamlit as st\n"
        "from tutor_screen.origin_search import query_changed, selection_changed\n"
        "event = st.session_state.pop('_test_origin_event', '')\n"
        "if event == 'query': query_changed()\n"
        "if event == 'selection': selection_changed()\n"
        f"runpy.run_path({str(APP)!r}, run_name='__main__')\n", default_timeout=15)
    app.session_state["page"] = "地图设置"
    app.run()
    assert not app.exception
    return app


def origin_event(app, **event):
    state = dict(app.session_state["map_origin_autocomplete"])
    state.update(event)
    app.session_state["map_origin_autocomplete"] = state
    app.session_state["_test_origin_event"] = next(iter(event))
    app.run()
    assert not app.exception


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
    assert not any(item.key in {"map_origin_search", "map_pick_origin"} for item in origins.button)
    assert not any(item.label == "出发点候选" for item in origins.selectbox)
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


@pytest.mark.parametrize("credentials,show_warning", [
    (MapCredentials(), False), (MapCredentials(web_key="   "), False),
    (MapCredentials(web_key="SYNTHETIC"), True),
    (MapCredentials(js_key="SYNTHETIC-JS"), True),
])
def test_import_origin_reminder_depends_on_saved_api_and_draft_selection(credentials, show_warning):
    origin = map_store.save_origin("学校", GeoPoint(lng=119.3, lat=26.1, city="福州市", label="合成学校 · 合成路1号"))
    map_store.save_settings(map_store.MapSettings(default_city="福州市", credentials=credentials))
    with patch("tutor_screen.maps.AmapClient._request", side_effect=AssertionError("No automatic requests")):
        app = AppTest.from_file(APP, default_timeout=15)
        app.session_state["page"] = "订单导入"
        app.run()
        assert not app.exception
        controls = app.container(key="map_filter_controls")
        assert controls.selectbox(key="map_origin_draft").options == ["未设置出发点", "学校 · 合成学校 · 合成路1号 · 福州市"]
        assert any("origin-selection-warning" in item.value for item in controls.columns[0].markdown) is show_warning
        controls.selectbox(key="map_origin_draft").set_value(origin.id).run()
        assert not any("origin-selection-warning" in item.value for item in app.markdown)
        assert app.session_state["map_applied"]["origin_id"] == ""
        app.selectbox(key="map_origin_draft").set_value("").run()
        assert any("origin-selection-warning" in item.value for item in app.markdown) is show_warning
        assert not app.exception


def test_import_reminds_when_api_is_configured_but_no_saved_origins_exist():
    map_store.save_settings(map_store.MapSettings(credentials=MapCredentials(web_key="SYNTHETIC")))
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["page"] = "订单导入"
    app.run()
    assert not app.exception
    assert any("请选择出发点" in item.value for item in app.container(key="map_filter_controls").columns[0].markdown)


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
    location = GeoPoint(lng=120.7, lat=28.0, label="合成学校 · 温州市鹿城区合成路1号", city="温州")
    with patch("tutor_screen.maps.AmapClient.search", return_value=[location]) as search:
        origin_event(app, query="合成学校")
    search.assert_called_once_with("合成学校", "温州")
    assert not any(item.key == "map_origin_save" for item in app.button)
    origin_event(app, selection={"query": "合成学校", "index": 0})
    app.button(key="map_origin_save").click().run()
    assert not app.exception
    saved = map_store.list_origins()
    assert len(saved) == 1 and saved[0].name == "学校"
    assert saved[0].point == location
    expected = "学校 · 合成学校 · 温州市鹿城区合成路1号 · 温州"
    assert expected in [item.value for item in app.markdown]
    reopened = setup_app()
    assert expected in [item.value for item in reopened.markdown]
    app.radio(key="nav_widget").set_value("筛选结果").run()
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.selectbox(key="map_origin_draft").set_value(saved[0].id).run()
    assert app.session_state["map_applied"]["origin"] is None
    app.button(key="apply_filters").click().run()
    assert app.session_state["map_applied"]["origin_id"] == saved[0].id
    assert map_store.load_settings().active_origin_id == saved[0].id


@pytest.mark.parametrize("label", ["", "学校", "合成学校 · 合成路123号"])
def test_saved_origins_show_address_or_explicit_legacy_placeholder(label):
    origin = map_store.save_origin("学校", GeoPoint(lng=120.7, lat=28, label=label, city="温州市"))
    with patch("tutor_screen.maps.AmapClient._request", side_effect=AssertionError("No implicit lookup")):
        app = setup_app()
    address = label if label and label != "学校" else "详细地址未保存，请重新选择地点"
    card = app.container(key=f"map_saved_origin_{origin.id}")
    assert card.markdown[0].value == f"学校 · {address} · 温州市"
    assert map_store.list_origins()[0] == origin
    card.button(key=f"map_delete_origin_{origin.id}").click().run()
    assert not app.exception
    assert map_store.list_origins() == []


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


@pytest.mark.parametrize("default_city,mismatch", [("温州市", True), ("", True), ("福州", False), (" 福州市 ", False)])
def test_origin_city_mismatch_requires_correct_default_before_save(default_city, mismatch):
    map_store.save_settings(map_store.MapSettings(default_city=default_city, credentials=MapCredentials(web_key="SYNTHETIC")))
    app = setup_app()
    point = GeoPoint(lng=119.3, lat=26.1, label="合成学校", city="福州市")
    app.text_input(key="map_origin_name").set_value("学校")
    with patch("tutor_screen.maps.AmapClient.search", return_value=[point]):
        origin_event(app, query="合成学校")
    origin_event(app, selection={"query": "合成学校", "index": 0})
    assert app.button(key="map_origin_save").disabled is mismatch
    assert any("请重新填写默认城市" in item.value for item in app.markdown) is mismatch
    if mismatch:
        assert not map_store.list_origins()
        app.text_input(key="map_default_city").set_value("福州市").run()
        assert app.button(key="map_origin_save").disabled
        assert any("请先保存地图设置" in item.value for item in app.markdown)
        with patch("tutor_screen.maps.AmapClient.search", return_value=[point]):
            app.button(key="map_save_settings").click().run()
            origin_event(app, query="合成学校")
        origin_event(app, selection={"query": "合成学校", "index": 0})
    assert not app.button(key="map_origin_save").disabled
    app.button(key="map_origin_save").click().run()
    assert len(map_store.list_origins()) == 1


def test_city_warning_clears_when_search_selection_is_cleared():
    map_store.save_settings(map_store.MapSettings(default_city="温州市", credentials=MapCredentials(web_key="SYNTHETIC")))
    app = setup_app()
    with patch("tutor_screen.maps.AmapClient.search", return_value=[GeoPoint(lng=119.3, lat=26.1, city="福州市")]):
        origin_event(app, query="学校")
    origin_event(app, selection={"query": "学校", "index": 0})
    assert app.button(key="map_origin_save").disabled
    origin_event(app, query="")
    assert not any("origin-city-warning" in item.value for item in app.markdown)


@pytest.mark.parametrize("result", [[], MapAPIError("合成查询失败")])
def test_origin_query_change_clears_previous_selection_and_reports_empty_or_error(result):
    map_store.save_settings(map_store.MapSettings(default_city="福州", credentials=MapCredentials(web_key="SYNTHETIC")))
    app = setup_app()
    point = GeoPoint(lng=119.3, lat=26.1, label="合成学校", city="福州")
    with patch("tutor_screen.maps.AmapClient.search", return_value=[point]):
        origin_event(app, query="学校")
    origin_event(app, selection={"query": "学校", "index": 0})
    assert app.session_state["map_origin_selected"] == point
    with patch("tutor_screen.maps.AmapClient.search", **({"side_effect": result} if isinstance(result, Exception) else {"return_value": result})):
        origin_event(app, query="新地点")
    assert app.session_state["map_origin_selected"] is None
    assert app.session_state["map_origin_candidates"] == []
    assert app.session_state["map_origin_message"]
    origin_event(app, selection={"query": "学校", "index": 0})
    assert app.session_state["map_origin_selected"] is None
    with patch("tutor_screen.maps.AmapClient.search") as search:
        origin_event(app, query="   ")
        app.run()
    search.assert_not_called()
    assert app.session_state["map_origin_message"] == ""
    assert not any(item.key == "map_origin_save" for item in app.button)


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
