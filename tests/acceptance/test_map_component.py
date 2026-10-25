"""Map bridge boundary tests; browser interactions use an isolated mocked SDK."""

from pathlib import Path

import pytest

from tutor_screen import map_component


def payload(points=(), origin=None, **changes):
    arguments = dict(
        points=points, origin=origin, selected_id=None, picking=False,
        js_key="browser-key", security_code="browser-code", height=560, focus_token=None,
    )
    arguments.update(changes)
    return map_component._prepare_map_data(**arguments)


def test_browser_payload_has_strict_field_allowlist_and_gcj_coordinates():
    order = {
        "id": "order-7", "number": "W007", "lng": "120.69991", "lat": "27.99492",
        "subject": "数学", "grade": "初二", "pay": "150 元/小时", "status": "possible",
        "distance_label": "约 2.3 公里", "raw_text": "老师联系电话：13800138000",
        "address": "房号 201", "rest_key": "NEVER-SEND-REST-KEY",
    }
    origin = {
        "lng": 120.7, "lat": 28, "label": "学校", "address": "宿舍 123",
        "server_key": "NEVER-SEND-REST-KEY",
    }
    result = payload([order], origin)
    assert set(result["points"][0]) == {
        "id", "number", "lng", "lat", "status", "subject", "grade", "pay", "distance_label", "precision", "location_label", "area_scale", "raw_text"
    }
    assert result["points"][0]["lng"] == 120.69991
    assert result["points"][0]["lat"] == 27.99492
    assert result["origin"] == {"lng": 120.7, "lat": 28.0, "label": "学校"}
    assert "NEVER-SEND" not in str(result)
    assert result["points"][0]["raw_text"] == order["raw_text"]
    assert "address" not in str(result)


def test_local_raw_preview_preserves_full_text_and_line_breaks():
    raw = "原始订单\n地址：合成小区\n<img src=x onerror=alert(1)>\n" + "详细要求。" * 1000
    result = payload([{"id": "long", "lng": 120, "lat": 28, "raw_text": raw}])
    assert result["points"][0]["raw_text"] == raw


@pytest.mark.parametrize("scale", ["small", "medium", "large", None, "invalid", "", 1, [], {}])
def test_area_scale_allowlist(scale):
    point = {"id": "area", "lng": 120, "lat": 28, "precision": "area", "area_scale": scale}
    assert payload([point])["points"][0]["area_scale"] == (scale if scale in ("small", "medium", "large") else None)


def test_area_scale_only_has_meaning_for_area_points():
    assert payload([{"id": "exact", "lng": 120, "lat": 28, "precision": "exact", "area_scale": "small"}])["points"][0]["area_scale"] is None


@pytest.mark.parametrize(
    "coords",
    [
        {"lng": float("nan"), "lat": 28},
        {"lng": 120, "lat": float("inf")},
        {"lng": -181, "lat": 28},
        {"lng": 120, "lat": 91},
        {"lng": True, "lat": 28},
        {"lng": "", "lat": 28},
        {"lng": 120},
    ],
)
def test_invalid_coordinates_never_reach_map(coords):
    result = payload([{"id": "invalid", **coords}], {"label": "invalid", **coords})
    assert result["points"] == []
    assert result["origin"] is None


def test_duplicate_or_missing_ids_are_not_ambiguous_map_markers():
    good = {"id": "same", "lng": 120, "lat": 28}
    result = payload([good, {**good, "lng": 121}, {"lng": 120, "lat": 28}])
    assert len(result["points"]) == 1
    assert result["points"][0]["lng"] == 120


def test_render_wrapper_wires_one_shot_event_keys_without_rest_credentials(monkeypatch):
    captured = {}

    def renderer(**kwargs):
        captured.update(kwargs)
        return "component-result"

    monkeypatch.setattr(map_component, "_MAP", renderer)
    callback = lambda: None
    assert map_component.render_map(
        [], origin=None, selected_id="x", picking=True,
        js_key=" browser-key ", security_code=" browser-code ", height=200,
        focus_token=2, key="map-test", on_selected_change=callback,
    ) == "component-result"
    assert captured["on_selected_change"] is callback
    assert callable(captured["on_picked_change"])
    assert captured["key"] == "map-test"
    assert captured["data"]["height"] == 320
    assert captured["data"]["focus_token"] == "2"
    assert captured["data"]["js_key"] == "browser-key"
    assert captured["data"]["picking"] is True
    assert "default" not in captured  # Trigger values must not persist as state.


def test_map_frontend_does_not_interpolate_untrusted_html_or_query_addresses():
    source = Path(map_component.__file__).with_name("map_frontend.js").read_text(encoding="utf-8")
    assert "node.textContent = String(text)" in source
    for forbidden in [
        "innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function",
        "restapi.amap.com", "navigator.geolocation", "setComponentValue",
        "postMessage(",
        "AMap.Circle", "AMap.Polygon",
    ]:
        assert forbidden not in source
    assert "setTriggerValue" in source
    assert "AMap.MarkerCluster" in source


def test_component_imported_before_runtime_mounts_in_multiple_apptests():
    # This module imported map_component in pytest's bare runtime already.
    # Each AppTest has a new component registry but the Python module is cached.
    from streamlit.testing.v1 import AppTest

    script = """
import streamlit as st
from tutor_screen.map_component import render_map
st.checkbox('Unrelated input', key='unrelated')
result = render_map([], key='map-runtime-check')
assert result.selected is None and result.picked is None
st.caption('map mounted')
"""
    for _ in range(2):
        app = AppTest.from_string(script).run()
        assert not app.exception
        assert app.caption[0].value == "map mounted"
        app.checkbox[0].check().run()
        assert not app.exception
