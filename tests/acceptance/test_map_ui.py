"""Independent acceptance of map setup, applied filters and local data scope."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen import map_store, map_ui
from tutor_screen.local_parser import LOCAL_FIELDS_REVISION
from tutor_screen.maps import GeoPoint, GeoResult, MapCredentials
from tutor_screen.models import Order

APP = Path(__file__).resolve().parents[2] / "app.py"


@pytest.fixture(autouse=True)
def map_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(map_store, "MAP_PATH", tmp_path / "maps.sqlite3")
    def no_network(*args, **kwargs):
        raise AssertionError("Map acceptance must never use real HTTP")
    monkeypatch.setattr("tutor_screen.maps.AmapClient._request", no_network)
    # Ordinary acceptance scenarios have unresolved synthetic addresses. Return
    # a final negative result instead of reaching the network; dedicated tests
    # below override locate to assert the automatic-query contract.
    monkeypatch.setattr("tutor_screen.maps.AmapClient.locate", lambda *args: GeoResult(status="not_found", message="合成未找到", resolver_revision=3))


def configure_maps():
    origin = map_store.save_origin("学校", GeoPoint(lng=120.70, lat=28.00, city="温州", source="user"))
    settings = map_store.MapSettings(
        default_city="温州", active_origin_id=origin.id,
        credentials=MapCredentials(js_key="SYNTHETIC-JS", security_code="SYNTHETIC-SECURITY", web_key="SYNTHETIC-WEB-PRIVATE"),
    )
    map_store.save_settings(settings)
    return origin


def order(number, address, *, rate=150.0):
    return Order(
        order_id=f"ORD-{number}", order_number=number,
        raw_text=f"订单号 {number} 小学数学 {rate}/h 线下 周六晚上 每周1次 每次2小时 地址：{address}",
        grade="小学", subjects=["数学"], hourly_rate=rate, mode="线下",
        days=[6], weekly_sessions=1, lesson_hours=2, time_slots=["晚上"],
        address=address, teacher_gender="不限", match_status="明确匹配", extraction_source="model",
    )


def open_results(orders):
    app = AppTest.from_file(APP, default_timeout=20)
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = orders
    app.session_state["filtered"] = orders
    app.session_state["_local_fields_revision"] = LOCAL_FIELDS_REVISION
    app.run()
    assert not app.exception
    return app


def all_text(app):
    return "\n".join(element.value for kind in [app.markdown, app.caption, app.info, app.warning, app.error] for element in kind)


@pytest.mark.parametrize("has_origin,sort_mode", [(True, "距离由近到远"), (False, "距离由近到远"), (True, "原始顺序")])
def test_distance_sort_has_stable_real_reference_and_unknown_segments(monkeypatch, has_origin, sort_mode):
    origin = GeoPoint(lng=120, lat=0)
    specs = [
        ("UNKNOWN-B", None, None), ("AREA10-A", 10, "area"),
        ("REAL30", 30, "exact"), ("AREA1-A", 1, "area"),
        ("REAL2-B", 2, "approximate"), ("UNKNOWN-A", None, None),
        ("REAL2-A", 2, "exact"), ("AREA10-B", 10, "area"),
        ("AREA1-B", 1, "area"), ("REAL0", 0, "exact"), ("AREA0", 0, "area"),
    ]
    values, geos = [], {}
    for number, km, precision in specs:
        value = order(number, "合成地址" + number)
        values.append(value)
        if km is None:
            geos[value.order_id] = GeoResult(status="not_found")
        else:
            point = GeoPoint(lng=120, lat=km / 111.19508, precision=precision)
            # 区域点优先于原始候选，不能因候选有精确坐标而进入第一段。
            geos[value.order_id] = GeoResult(status="located", candidates=[origin], area_point=point) if precision == "area" else GeoResult(status="located", candidates=[point])
    monkeypatch.setattr(map_ui, "ensure_state", lambda: None)
    monkeypatch.setattr(map_ui, "st", SimpleNamespace(session_state=SimpleNamespace(map_applied={"sort": sort_mode})))
    monkeypatch.setattr(map_ui, "applied_origin", lambda: origin if has_origin else None)
    monkeypatch.setattr(map_ui, "lookup_geo", lambda value: geos[value.order_id])
    original = list(values)
    result = map_ui.filter_orders(values)
    expected = ["REAL0", "REAL2-B", "REAL2-A", "REAL30", "AREA0", "AREA1-A", "AREA1-B", "AREA10-A", "AREA10-B", "UNKNOWN-B", "UNKNOWN-A"]
    assert [value.order_number for value in result] == (expected if has_origin and sort_mode == "距离由近到远" else [number for number, _, _ in specs])
    assert values == original and result is not values
    for value, (_, km, precision) in zip(values, specs):
        if has_origin and precision == "area":
            assert map_ui.order_distance(value) is None
            assert map_ui._reference_distance(value) == pytest.approx(km, abs=.001)
        else:
            assert map_ui._reference_distance(value) is None


def test_restored_batch_and_refresh_use_the_same_three_segment_sort(monkeypatch):
    from tutor_screen import batch_store

    origin = configure_maps().point
    specs = [("UNKNOWN", None, None), ("AREA10", 10, "area"), ("REAL30", 30, "exact"),
             ("AREA1", 1, "area"), ("REAL2", 2, "exact")]
    values, geos = [], {}
    for number, km, precision in specs:
        values.append(order(number, "合成地址" + number))
        if km is None:
            geos[number] = GeoResult(status="not_found", resolver_revision=3)
        else:
            point = GeoPoint(lng=origin.lng, lat=origin.lat + km / 111.19508, precision=precision)
            geos[number] = GeoResult(status="ambiguous", area_point=point, resolver_revision=3) if precision == "area" else GeoResult(status="located", candidates=[point])
    monkeypatch.setattr(map_ui, "lookup_geo", lambda value: geos[value.order_number])
    batch_store.save_batch(batch_store.SavedBatch(input_text="\n".join(value.raw_text for value in values), orders=values))
    app = AppTest.from_file(APP, default_timeout=20).run()
    expected = ["REAL2", "REAL30", "AREA1", "AREA10", "UNKNOWN"]
    assert not app.exception
    assert [value.order_number for value in app.session_state["filtered"]] == expected
    app.button(key="apply_filters").click().run()
    assert not app.exception
    assert [value.order_number for value in app.session_state["filtered"]] == expected


def test_without_map_keys_original_orders_and_filters_still_work():
    value = order("MAP100", "八仙楼小区")
    app = open_results([value])
    assert any(element.value == value.raw_text for element in app.text)
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.number_input(key="filter_min_rate").set_value(200.0).run()
    app.button(key="apply_filters").click().run()
    assert not app.exception and app.session_state["filtered"] == []
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.button(key="reset_filters").click().run()
    assert not app.exception and len(app.session_state["filtered"]) == 1


def test_empty_results_links_to_import_filters_without_service_calls():
    with patch("tutor_screen.maps.AmapClient._request", side_effect=AssertionError("No automatic query")):
        app = open_results([])
        assert app.container(key="results_map") is not None
        assert not any(item.key == "filter_panel" for item in app.container)
        assert app.button(key="edit_filters") is not None
        assert app.container(key="results_list").info[0].value.startswith("还没有订单")
        app.button(key="edit_filters").click().run()
        assert app.container(key="filter_panel").button(key="apply_filters") is not None
        app.number_input(key="filter_min_rate").set_value(120.0).run()
        app.button(key="apply_filters").click().run()
    assert not app.exception
    assert app.session_state["criteria"].min_hourly_rate == 120.0
    assert app.session_state["filtered"] == []


def test_ordinary_render_navigation_and_filtering_reuse_initial_auto_query():
    configure_maps()
    app = open_results([order("MAP100", "八仙楼小区")])
    with patch("tutor_screen.maps.AmapClient.locate") as locate, patch("tutor_screen.maps.AmapClient.search") as search, patch("tutor_screen.maps.AmapClient.commute") as commute:
        app.run()
        app.radio(key="nav_widget").set_value("订单导入").run()
        app.number_input(key="filter_min_rate").set_value(100.0).run()
        app.button(key="apply_filters").click().run()
        for page in ["地图设置", "订单导入", "筛选结果"]:
            app.radio(key="nav_widget").set_value(page).run()
            assert not app.exception
        locate.assert_not_called()
        search.assert_not_called()
        commute.assert_not_called()


def test_distance_sort_keeps_near_far_and_unknown_orders_after_apply():
    configure_maps()
    near, far, unknown = order("NEAR", "近处小区"), order("FAR", "远处小区"), order("UNKNOWN", "待确认地址")
    map_store.save_manual(near.address, "温州", GeoPoint(lng=120.701, lat=28.001, city="温州", source="user"))
    map_store.save_manual(far.address, "温州", GeoPoint(lng=121.10, lat=28.40, city="温州", source="user"))
    app = open_results([unknown, far, near])
    app.radio(key="nav_widget").set_value("订单导入").run()
    controls = app.container(key="map_filter_controls")
    assert not controls.checkbox and not controls.number_input
    assert app.selectbox(key="map_sort_draft").value == "距离由近到远"
    app.selectbox(key="map_sort_draft").set_value("原始顺序").run()
    assert [item.order_id for item in app.session_state["filtered"]] == [near.order_id, far.order_id, unknown.order_id]
    app.button(key="apply_filters").click().run()
    assert not app.exception
    assert [item.order_id for item in app.session_state["filtered"]] == [unknown.order_id, far.order_id, near.order_id]
    assert "距离待确认" in all_text(app) or "距离未知" in all_text(app)


def test_upgrading_active_distance_filters_restores_hidden_orders_without_reparse():
    configure_maps()
    near, far, unknown = order("NEAR", "近处小区"), order("FAR", "远处小区"), order("UNKNOWN", "待确认地址")
    map_store.save_manual(near.address, "温州", GeoPoint(lng=120.701, lat=28.001, city="温州", source="user"))
    map_store.save_manual(far.address, "温州", GeoPoint(lng=121.10, lat=28.40, city="温州", source="user"))
    app = open_results([near, far, unknown])
    app.session_state["filtered"] = [near]
    app.session_state["map_applied"].update(radius_km=1.0, hide_unknown=True, sort="原始顺序")
    del app.session_state["_map_distance_sort_default"]
    app.session_state["map_radius_enabled"] = True
    app.session_state["map_radius_km"] = 1.0
    app.session_state["map_hide_unknown"] = True
    with patch("tutor_screen.pipeline.process_batch") as model:
        app.run()
        model.assert_not_called()
    assert not app.exception
    assert {item.order_id for item in app.session_state["filtered"]} == {near.order_id, far.order_id, unknown.order_id}
    assert not {"radius_km", "hide_unknown"}.intersection(app.session_state["map_applied"])
    assert app.session_state["map_applied"]["sort"] == app.session_state["map_sort_draft"] == "距离由近到远"
    for key in ("map_radius_enabled", "map_radius_km", "map_hide_unknown"):
        assert key not in app.session_state


def test_origin_switch_is_draft_and_recalculates_only_when_applied():
    first = configure_maps()
    second = map_store.save_origin("住处", GeoPoint(lng=121.10, lat=28.40, city="温州", source="user"))
    value = order("MAP100", "学校旁小区")
    map_store.save_manual(value.address, "温州", first.point)
    app = open_results([value])
    before = app.session_state["map_applied"]
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.selectbox(key="map_origin_draft").set_value(second.id).run()
    assert app.session_state["map_applied"] == before
    app.button(key="apply_filters").click().run()
    assert not app.exception and app.session_state["map_applied"]["origin_id"] == second.id
    assert app.session_state["map_applied"]["origin"]["lng"] == second.point.lng


def test_distance_sort_defaults_and_reset_use_nearest_first_and_unknown_last():
    configure_maps()
    far, unknown, near = order("FAR", "远处小区"), order("UNKNOWN", "地址不详"), order("NEAR", "近处小区")
    map_store.save_manual(far.address, "温州", GeoPoint(lng=120.80, lat=28.10, city="温州", source="user"))
    map_store.save_manual(near.address, "温州", GeoPoint(lng=120.701, lat=28.001, city="温州", source="user"))
    app = open_results([far, unknown, near])
    expected = [near.order_id, far.order_id, unknown.order_id]
    assert [item.order_id for item in app.session_state["filtered"]] == expected
    app.radio(key="nav_widget").set_value("订单导入").run()
    assert app.selectbox(key="map_sort_draft").value == "距离由近到远"
    app.selectbox(key="map_sort_draft").set_value("原始顺序").run()
    app.button(key="apply_filters").click().run()
    assert [item.order_id for item in app.session_state["filtered"]] == [far.order_id, unknown.order_id, near.order_id]
    app.button(key="edit_filters").click().run()
    assert app.selectbox(key="map_sort_draft").value == "原始顺序"
    app.button(key="reset_filters").click().run()
    assert not app.exception and app.selectbox(key="map_sort_draft").value == "距离由近到远"
    assert [item.order_id for item in app.session_state["filtered"]] == expected


def test_auto_query_only_includes_unresolved_applied_filtered_addresses():
    configure_maps()
    cached, unknown, excluded = order("CACHED", "已定位小区"), order("UNKNOWN", "新小区"), order("EXCLUDED", "不符合小区", rate=50)
    map_store.save_geo(cached.address, "温州", GeoResult(status="located", candidates=[GeoPoint(lng=120.7, lat=28.0, city="温州")]))
    app = AppTest.from_file(APP, default_timeout=20)
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [cached, unknown, excluded]
    from tutor_screen.models import Criteria
    app.session_state["criteria"] = Criteria(min_hourly_rate=100)
    with patch("tutor_screen.maps.AmapClient.locate", return_value=GeoResult(status="not_found", resolver_revision=3)) as locate:
        app.run()
        locate.assert_called_once_with(unknown.address, "温州")
    assert not app.exception
    batch = app.session_state["map_batch"]
    assert batch["total"] == 1
    assert batch["done"] == 1 and not batch["running"]
    assert excluded.address not in str(batch)
    assert cached.raw_text not in str(batch) and "SYNTHETIC-WEB-PRIVATE" not in str(batch)
    assert not any(b.key in {"map_prepare_batch", "map_start_batch"} for b in app.button)


def test_deleting_order_history_preserves_saved_origins_and_map_settings():
    origin = configure_maps()
    app = open_results([order("MAP100", "八仙楼小区")])
    app.radio(key="nav_widget").set_value("开发日志").run()
    app.button(key="clear_history").click().run()
    app.button(key="confirm_clear_history").click().run()
    assert not app.exception
    assert map_store.list_origins() == [origin]
    assert map_store.load_settings().credentials.web_key == "SYNTHETIC-WEB-PRIVATE"


def test_removed_inspector_and_commute_controls_do_not_return_after_card_selection():
    configure_maps()
    value = order("MAP100", "八仙楼小区")
    app = open_results([value])
    with patch("tutor_screen.maps.AmapClient.commute") as commute:
        app.button(key=f"map_locate_{value.order_id}").click().run()
        app.run()
        commute.assert_not_called()
    assert not app.exception
    assert app.session_state["map_selected_order"] == value.order_id
    assert "通勤比较" not in all_text(app)
    assert not any(item.key == "map_selected_order_picker" for item in app.selectbox)
    assert not any(item.key == "map_commute_query" for item in app.button)
    assert not any(item.label == "结果视图" for item in app.segmented_control)


def test_selecting_another_card_discards_previous_candidates_and_picked_point():
    configure_maps()
    first, second = order("FIRST", "第一小区"), order("SECOND", "第二小区")
    app = open_results([first, second])
    app.session_state["map_selected_order"] = first.order_id
    app.session_state["map_order_candidates"] = [GeoPoint(lng=120.701, lat=28.001, label="仅属于第一单")]
    app.session_state["map_picked_point"] = GeoPoint(lng=120.702, lat=28.002)
    app.session_state["map_picking"] = "order:" + first.order_id
    app.run()
    app.button(key=f"map_locate_{second.order_id}").click().run()
    assert not app.exception
    assert app.session_state["map_selected_order"] == second.order_id
    assert app.session_state["map_order_candidates"] == []
    assert app.session_state.get("map_picked_point") is None
    assert app.session_state["map_picking"] == ""


def test_map_settings_and_origin_search_save_survive_new_session_without_automatic_requests():
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.radio(key="nav_widget").set_value("地图设置").run()
    for key, value in {"map_js_key": "SYNTHETIC-JS", "map_security_code": "SYNTHETIC-SECURITY", "map_web_key": "SYNTHETIC-WEB-PRIVATE", "map_default_city": "温州市"}.items():
        app.text_input(key=key).set_value(value)
    app.button(key="map_save_settings").click().run()
    assert not app.exception and map_store.load_settings().default_city == "温州市"
    app.text_input(key="map_origin_name").set_value("学校")
    app.text_input(key="map_origin_query").set_value("测试学校")
    with patch("tutor_screen.maps.AmapClient.search", return_value=[GeoPoint(lng=120.7, lat=28.0, city="温州市", label="测试学校")]) as search:
        app.button(key="map_origin_search").click().run()
        search.assert_called_once_with("测试学校", "温州市")
    app.button(key="map_origin_save").click().run()
    assert not app.exception and [value.name for value in map_store.list_origins()] == ["学校"]
    reopened = AppTest.from_file(APP, default_timeout=20).run()
    reopened.radio(key="nav_widget").set_value("地图设置").run()
    assert not reopened.exception
    assert reopened.text_input(key="map_web_key").value == "SYNTHETIC-WEB-PRIVATE"
    assert reopened.text_input(key="map_default_city").value == "温州市"
    assert "学校" in all_text(reopened)
    assert b"SYNTHETIC-WEB-PRIVATE" not in map_store.MAP_PATH.read_bytes()


def test_order_raw_is_available_but_collapsed_for_compact_browsing():
    value = order("MAP100", "八仙楼小区")
    app = open_results([value])
    raw = next(item for item in app.expander if item.label == "查看原始订单")
    assert raw.proto.expanded is False
    assert any(item.value == value.raw_text for item in raw.text)
    assert app.button(key=f"map_locate_{value.order_id}").label == "在地图中定位 ↑"


def test_clearing_map_cache_updates_active_distance_results_and_keeps_user_confirmed_points():
    configure_maps()
    cached, manual = order("CACHE", "接口缓存地址"), order("MANUAL", "用户确认地址")
    point = GeoPoint(lng=120.701, lat=28.001, city="温州")
    map_store.save_geo(cached.address, "温州", GeoResult(status="located", candidates=[point]))
    map_store.save_manual(manual.address, "温州", point)
    app = open_results([cached, manual])
    app.radio(key="nav_widget").set_value("订单导入").run()
    app.button(key="apply_filters").click().run()
    assert len(app.session_state["filtered"]) == 2
    app.radio(key="nav_widget").set_value("地图设置").run()
    app.button(key="map_clear_cache").click().run()
    app.radio(key="nav_widget").set_value("筛选结果").run()
    assert not app.exception
    assert {value.order_id for value in app.session_state["filtered"]} == {cached.order_id, manual.order_id}
    assert len(map_store.list_origins()) == 1 and map_store.get_manual(manual.address, "温州") is not None
