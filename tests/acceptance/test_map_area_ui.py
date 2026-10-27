"""区域显示、距离排序、持久缓存和旧批次升级的页面回归。"""
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from tutor_screen import map_store
from tutor_screen.local_parser import parse_local
from tutor_screen.maps import GeoPoint, GeoResult, LOCATION_REVISION, MapCredentials
from tutor_screen.models import Order


APP = Path(__file__).resolve().parents[2] / "app.py"


def setup():
    origin = map_store.save_origin("合成学校", GeoPoint(lng=120.7, lat=28, city="温州市", precision="exact", source="user"))
    map_store.save_settings(map_store.MapSettings(default_city="温州市", active_origin_id=origin.id, persist_api_cache=True,
        credentials=MapCredentials(js_key="synthetic-js", security_code="synthetic-security", web_key="synthetic-web")))


def order(number, address):
    raw = f"订单号 {number} 小学数学 150/h 线下 每周1次 周六晚上 每次2小时 地址：{address}"
    return Order(**parse_local(raw).model_dump(), order_id=number, raw_text=raw, extraction_source="model")


def test_area_marker_keeps_reference_distance_and_all_orders_when_sorting():
    setup()
    area, far = order("AREA101", "鹿城区人民路"), order("AREA102", "远处示例小区")
    reference = GeoPoint(lng=121.4, lat=28.4, label="温州市鹿城区人民路", city="温州市", precision="area", area_scale="medium")
    map_store.save_geo(area.address, "温州市", GeoResult(status="ambiguous", area_point=reference, resolver_revision=LOCATION_REVISION))
    map_store.save_geo(far.address, "温州市", GeoResult(status="located", resolver_revision=LOCATION_REVISION, candidates=[GeoPoint(lng=121, lat=28.3, city="温州市", precision="exact")]))
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [area, far]
    app.session_state["filtered"] = [area, far]
    with patch("tutor_screen.map_component.render_map") as render, patch("tutor_screen.maps.AmapClient._request") as network:
        app.run()
        assert not app.exception
        points = render.call_args.kwargs["points"]
        marker = next(point for point in points if point["id"] == area.order_id)
        assert marker["precision"] == "area" and marker["location_label"] == reference.label
        assert marker["area_scale"] == "medium"
        assert "区域参考约" in marker["distance_label"] and "实际距离待确认" in marker["distance_label"]
        assert any("区域约略标记 1 单" in item.value for item in app.caption)
        assert any("地图显示 2 单" in item.value for item in app.caption)
        assert any("约略位置：温州市鹿城区人民路" in item.value for item in app.caption)
        app.button(key="edit_filters").click().run()
        app.selectbox(key="map_sort_draft").set_value("距离由近到远").run()
        app.button(key="apply_filters").click().run()
        assert not app.exception and [o.order_id for o in app.session_state["filtered"]] == [far.order_id, area.order_id]
        app.radio(key="nav_widget").set_value("订单导入").run()
        assert not app.container(key="map_filter_controls").checkbox
        app.button(key="apply_filters").click().run()
        assert not app.exception and len(app.session_state["filtered"]) == 2
        network.assert_not_called()
    # 接口持久缓存可重新读取区域参考点，原始候选与状态不伪装成精确地址。
    map_store._MEMORY.clear()
    assert map_store.get_geo(area.address, "温州市").area_point == reference


def test_previous_unresolved_cache_refreshes_once_and_places_road_marker():
    setup()
    value = order("AREA201", "鹿城区人民路")
    map_store.save_geo(value.address, "温州市", GeoResult(status="ambiguous", resolver_revision=2))
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [value]
    app.session_state["filtered"] = [value]
    # 模拟升级前已经尝试过此地址的活跃会话。
    app.session_state["map_auto_scope"] = "previous-resolver-scope"
    with patch("tutor_screen.maps.AmapClient._request", return_value={"status": "1", "geocodes": [
        {"formatted_address": "温州市鹿城区人民路", "city": "温州市", "location": "120.71,28.01", "level": "道路"}
    ]}) as network, patch("tutor_screen.map_component.render_map") as render:
        app.run()
        app.run()
        assert not app.exception
        network.assert_called_once()
        assert render.call_args.kwargs["points"][0]["precision"] == "area"
        assert "地图显示 1 单" in "\n".join(item.value for item in app.caption)
        for _ in range(3):
            app.run()
        network.assert_called_once()
    saved = map_store.get_geo(value.address, "温州市")
    assert saved.resolver_revision == LOCATION_REVISION and saved.area_point


def test_online_orders_with_addresses_are_not_plotted_as_regions():
    setup()
    raw = "小学数学 150/h 仅线上\n地址：鹿城区人民路"
    value = Order(**parse_local(raw).model_dump(), order_id="ONLINE", raw_text=raw)
    app = AppTest.from_file(APP)
    app.session_state["page"] = "筛选结果"
    app.session_state["orders"] = [value]
    app.session_state["filtered"] = [value]
    with patch("tutor_screen.maps.AmapClient._request") as network, patch("tutor_screen.map_component.render_map") as render:
        app.run()
        assert not app.exception and render.call_args.kwargs["points"] == []
        network.assert_not_called()
