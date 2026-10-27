"""Small CCv2 bridge for the official AMap JavaScript 2.0 map.

Only display fields, local raw-text previews and browser credentials cross this
boundary. Raw text is rendered locally, never passed to SDK queries or options.
The REST service key and route queries stay on the Python side.
The JS/CSS files are loaded as inline strings (no frontend build step needed).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
from pathlib import Path
from threading import RLock
from typing import Any

import streamlit as st
from streamlit.errors import StreamlitAPIException


_ASSETS = Path(__file__).parent
_NAME = "tutor_amap_orders_v102"
_JS = _ASSETS.joinpath("map_frontend.js").read_text(encoding="utf-8")
_CSS = _ASSETS.joinpath("map_frontend.css").read_text(encoding="utf-8")
_REGISTRATION_LOCK = RLock()


def _register_map():
    return st.components.v2.component(
        _NAME,
        html='<div class="tutor-map-slot"></div>',
        js=_JS,
        css=_CSS,
        # AMap injects its own official stylesheet in document.head.
        isolate_styles=False,
    )


_MAP = _register_map()


def _coordinates(value: Mapping[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(value, Mapping):
        return None
    raw_lng, raw_lat = value.get("lng"), value.get("lat")
    if isinstance(raw_lng, bool) or isinstance(raw_lat, bool):
        return None
    try:
        lng, lat = float(raw_lng), float(raw_lat)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(lng) and math.isfinite(lat)):
        return None
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return None
    return lng, lat


def _label(value: Any, limit: int = 160) -> str:
    return str(value if value is not None else "")[:limit]


def _prepare_map_data(
    points: Sequence[Mapping[str, Any]],
    origin: Mapping[str, Any] | None,
    selected_id: str | None,
    picking: bool,
    js_key: str,
    security_code: str,
    height: int,
    focus_token: str | int | None,
) -> dict[str, Any]:
    """Allowlist map fields so whole order/config dictionaries never leak."""
    clean_points = []
    seen = set()
    for point in points:
        coords = _coordinates(point)
        if coords is None:
            continue
        identity = _label(point.get("id"), 256)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        clean_points.append(
            {
                "id": identity,
                "lng": coords[0],
                "lat": coords[1],
                "raw_text": str(point.get("raw_text") or ""),
                **{
                    field: _label(point.get(field))
                    for field in (
                        "number", "status", "subject", "grade", "pay", "distance_label", "location_label"
                    )
                },
                "precision": point.get("precision") if point.get("precision") in {"exact", "approximate", "area"} else "approximate",
                "area_scale": point.get("area_scale") if point.get("precision") == "area" and point.get("area_scale") in ("small", "medium", "large") else None,
            }
        )
    clean_origin = None
    origin_coords = _coordinates(origin)
    if origin_coords is not None:
        clean_origin = {
            "lng": origin_coords[0],
            "lat": origin_coords[1],
            "label": _label(origin.get("label") or "出发点"),
        }
    return {
        "points": clean_points,
        "origin": clean_origin,
        "selected_id": _label(selected_id, 256) if selected_id is not None else None,
        "picking": bool(picking),
        "js_key": str(js_key or "").strip(),
        "security_code": str(security_code or "").strip(),
        "height": max(320, min(int(height), 1000)),
        "focus_token": _label(focus_token, 80) if focus_token is not None else None,
    }


def render_map(
    points: Sequence[Mapping[str, Any]],
    *,
    origin: Mapping[str, Any] | None = None,
    selected_id: str | None = None,
    picking: bool = False,
    js_key: str = "",
    security_code: str = "",
    height: int = 560,
    focus_token: str | int | None = None,
    key: str = "amap_orders",
    on_selected_change: Callable[[], None] | None = None,
    on_picked_change: Callable[[], None] | None = None,
):
    """Render map, returning one-shot ``selected`` and ``picked`` triggers.

    ``selected``: {id, event_id}; ``picked``: {lng, lat, event_id}.
    Coordinates must be AMap/GCJ-02, in longitude then latitude order.
    ``focus_token`` can be incremented when locating the same card repeatedly.
    """
    arguments = dict(
        data=_prepare_map_data(
            points, origin, selected_id, picking, js_key, security_code, height, focus_token
        ),
        width="stretch",
        height="content",
        key=key,
        on_selected_change=on_selected_change or (lambda: None),
        on_picked_change=on_picked_change or (lambda: None),
    )
    try:
        return _MAP(**arguments)
    except StreamlitAPIException as error:
        # Component registries belong to the Streamlit runtime. AppTest and
        # embedded apps can replace that runtime while Python modules remain
        # imported. Re-register only for that case; ordinary reruns and sessions
        # reuse the single definition and never register on every mount.
        if f"Component '{_NAME}' is not registered" not in str(error):
            raise
        with _REGISTRATION_LOCK:
            try:
                return _MAP(**arguments)
            except StreamlitAPIException as second_error:
                if f"Component '{_NAME}' is not registered" not in str(second_error):
                    raise
            renderer = _register_map()
        return renderer(**arguments)
