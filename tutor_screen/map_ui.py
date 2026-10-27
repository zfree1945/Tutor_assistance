"""筛选后自动定位；地理信息独立于订单提取事实，不自动重试失败请求。"""

from __future__ import annotations

import hashlib
from html import escape
import time
from collections.abc import Callable
from urllib.parse import urlencode

import streamlit as st

from tutor_screen import map_store as store
from tutor_screen.origin_search import render_origin_search
from tutor_screen.maps import AmapClient, GeoPoint, GeoResult, MapAPIError, MapCredentials, distance_km, LOCATION_REVISION


MODES = ["公共交通", "骑行", "步行", "驾车"]


def _geo_key(address: str, city: str) -> str:
    return hashlib.sha256(f"{city.strip()}\0{address.strip()}".encode()).hexdigest()


def ensure_state() -> None:
    if not st.session_state.get("_maps_loaded"):
        try:
            settings = store.load_settings()
            origins = store.list_origins()
        except store.MapStoreError as exc:
            settings, origins = store.MapSettings(), []
            st.session_state.map_error = str(exc)
        st.session_state.map_settings = settings
        st.session_state.map_origins = origins
        st.session_state._maps_loaded = True
        selected = next((o for o in origins if o.id == settings.active_origin_id), None)
        st.session_state.map_applied = {
            "origin_id": selected.id if selected else "",
            "origin": selected.point.model_dump() if selected else None,
            "city": settings.default_city,
            "sort": "距离由近到远",
        }
    defaults = {
        "map_error": "", "map_geo_session": {}, "map_commute_session": {},
        "map_selected_order": "", "map_batch": None, "map_picking": "",
        "map_origin_candidates": [], "map_order_candidates": [],
        "map_origin_draft": st.session_state.map_applied["origin_id"],
        "map_sort_draft": "距离由近到远",
        "map_auto_attempted": set(), "map_auto_scope": "", "map_auto_epoch": 0,
        "map_auto_next_at": 0.0,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if not st.session_state.get("_map_distance_sort_default"):
        if st.session_state.map_applied["sort"] != "距离由近到远":
            st.session_state._map_results_dirty = True
        st.session_state.map_applied["sort"] = "距离由近到远"
        st.session_state.map_sort_draft = "距离由近到远"
        st.session_state._map_distance_sort_default = True
    # 升级中的会话也取消旧距离限制，并在结果页恢复被旧条件隐藏的订单。
    radius = st.session_state.map_applied.pop("radius_km", None)
    hide_unknown = st.session_state.map_applied.pop("hide_unknown", False)
    if radius is not None or hide_unknown:
        st.session_state._map_results_dirty = True
    for key in ("map_radius_enabled", "map_radius_km", "map_hide_unknown"):
        st.session_state.pop(key, None)


def _error(exc: Exception) -> None:
    st.session_state.map_error = str(exc) if isinstance(exc, (store.MapStoreError, MapAPIError)) else "地图操作未完成，请检查配置与网络后重试。"


def applied_origin() -> GeoPoint | None:
    ensure_state()
    value = st.session_state.map_applied.get("origin")
    return GeoPoint.model_validate(value) if value else None


def current_city() -> str:
    ensure_state()
    return st.session_state.map_applied.get("city", "")


def _manual_identity(order) -> str:
    # 模糊同名地址可对应不同订单；只保存摘要，不把原文放入位置数据库。
    return hashlib.sha256(f"{getattr(order, 'order_number', None) or ''}\0{order.raw_text}".encode()).hexdigest()


def lookup_geo(order) -> GeoResult | None:
    ensure_state()
    if not order.address or order.mode == "线上":
        return None
    address, city = order.address, current_city()
    key = _geo_key(address, city)
    try:
        manual = store.get_manual(address, city, order_key=_manual_identity(order)) or store.get_manual(address, city)
        if manual:
            return GeoResult(status="located", candidates=[manual])
        if key in st.session_state.map_geo_session:
            return st.session_state.map_geo_session[key]
        return store.get_geo(address, city)
    except (store.MapStoreError, MapAPIError) as exc:
        _error(exc)
        return None


def point_for(order) -> GeoPoint | None:
    result = lookup_geo(order)
    if not result:
        return None
    area = getattr(result, "area_point", None)
    return area or (result.candidates[0] if result.status == "located" and result.candidates else None)


def order_distance(order) -> float | None:
    origin, point = applied_origin(), point_for(order)
    return distance_km(origin, point) if origin and point and point.precision != "area" else None


def distance_label(order) -> str:
    value, point, origin = order_distance(order), point_for(order), applied_origin()
    if point and point.precision == "area":
        return f"区域参考约 {distance_km(origin, point):.0f} 公里，实际距离待确认" if origin else "区域已标记，实际距离待确认"
    if value is None:
        return "距离未知"
    approximate = point.precision != "exact" or origin.precision != "exact"
    return f"{'约 ' if approximate else ''}{value:.2f} 公里（直线）"


def _reference_distance(order) -> float | None:
    origin, point = applied_origin(), point_for(order)
    return distance_km(origin, point) if origin and point and point.precision == "area" else None


def filter_orders(orders: list) -> list:
    """按已应用排序排列订单；距离大小或未知均不排除订单。"""
    ensure_state()
    applied = st.session_state.map_applied
    result = list(orders)
    if applied["sort"] == "距离由近到远" and applied_origin() is not None:
        def distance_key(order):
            distance = order_distance(order)
            if distance is not None:
                return (0, distance)
            reference = _reference_distance(order)
            return (1, reference) if reference is not None else (2, 0)

        result.sort(key=distance_key)
    return result


def _origin_label(origin: store.SavedOrigin) -> str:
    address = origin.point.label.strip()
    if not address or address == origin.name:
        address = "详细地址未保存，请重新选择地点"
    return f"{origin.name} · {address} · {origin.point.city or '城市未标注'}"


def filter_widgets() -> None:
    ensure_state()
    origins = st.session_state.map_origins
    options = [""] + [o.id for o in origins]
    labels = {o.id: _origin_label(o) for o in origins}
    if st.session_state.map_origin_draft not in options:
        st.session_state.map_origin_draft = ""
    with st.container(key="map_filter_controls"):
        row = st.columns(2)
        row[0].selectbox("出发点", options, format_func=lambda x: labels.get(x, "未设置出发点"), key="map_origin_draft", persist_state="session")
        credentials = st.session_state.map_settings.credentials
        if (credentials.web_key.strip() or credentials.js_key.strip()) and not st.session_state.map_origin_draft:
            row[0].markdown('<div class="origin-selection-warning" role="alert">请选择出发点；如尚未添加，请先到“地图设置”保存常用出发点。</div>', unsafe_allow_html=True)
        row[1].selectbox("订单排序", ["距离由近到远", "原始顺序"], key="map_sort_draft", persist_state="session")
        st.caption("改动后点击“应用筛选”统一更新。距离仅用于展示和排序，距离未知的订单保留。")
        if not origins:
            st.caption("到“地图设置”选择默认城市并添加常用出发点。未配置地图也可继续导入和筛选。")


def apply_draft() -> bool:
    ensure_state()
    selected = next((o for o in st.session_state.map_origins if o.id == st.session_state.map_origin_draft), None)
    st.session_state.map_applied = {
        "origin_id": selected.id if selected else "", "origin": selected.point.model_dump() if selected else None,
        "city": st.session_state.map_settings.default_city, "sort": st.session_state.map_sort_draft,
    }
    st.session_state.map_settings.active_origin_id = selected.id if selected else ""
    try:
        store.save_settings(st.session_state.map_settings)
    except store.MapStoreError as exc:
        _error(exc)
    return True


def reset_draft() -> None:
    ensure_state()
    st.session_state.map_sort_draft = "距离由近到远"
    st.session_state.map_origin_draft = st.session_state.map_applied["origin_id"]
    st.session_state.map_applied.update(sort="距离由近到远")


def _client() -> AmapClient:
    return AmapClient(st.session_state.map_settings.credentials.web_key)


def _cache_geo(address: str, city: str, result: GeoResult) -> None:
    st.session_state.map_geo_session[_geo_key(address, city)] = result
    st.session_state._map_results_dirty = True
    if getattr(st.session_state.map_settings, "persist_api_cache", False):
        store.save_geo(address, city, result)


def prepare_batch(orders: list) -> dict:
    unique = {}
    for order in orders:
        if not order.address or order.mode == "线上":
            continue
        cached = lookup_geo(order)
        stale = (cached is not None and getattr(cached, "resolver_revision", 1) < LOCATION_REVISION
                 and not any(point.source == "user" for point in cached.candidates))
        if cached is None or cached.status == "failed" or stale:
            unique[_geo_key(order.address, current_city())] = {"address": order.address, "city": current_city()}
    return {"pending": list(unique.values()), "total": len(unique), "done": 0, "running": False, "cancelled": False, "stopped": "", "failures": []}


def sync_auto_batch(orders: list) -> None:
    """仅同步已应用筛选，草稿和普通重绘不产生重复查询。"""
    ensure_state()
    settings = st.session_state.map_settings
    if not all((settings.credentials.web_key, settings.credentials.js_key, settings.credentials.security_code)) or not current_city():
        if st.session_state.map_batch:
            st.session_state.map_batch["running"] = False
        return
    batch_orders = st.session_state.get("orders", orders)
    identity = st.session_state.get("current_import_id") or [(o.order_id, o.raw_text) for o in batch_orders]
    scope = hashlib.sha256(repr((identity, current_city(), settings.credentials.web_key, st.session_state.map_auto_epoch, LOCATION_REVISION)).encode()).hexdigest()
    if scope != st.session_state.map_auto_scope:
        st.session_state.map_auto_scope = scope
        st.session_state.map_auto_attempted = set()
        st.session_state.map_auto_next_at = 0.0
        st.session_state.map_batch = None
        st.session_state.map_error = ""
    batch = st.session_state.map_batch
    if batch and (batch["cancelled"] or batch["stopped"]):
        return
    eligible = [o for o in orders if o.match_status in {"明确匹配", "可能匹配"}]
    desired = [item for item in prepare_batch(eligible)["pending"]
               if _geo_key(item["address"], item["city"]) not in st.session_state.map_auto_attempted]
    if batch and batch["running"]:
        batch["pending"] = desired
        batch["total"] = batch["done"] + len(desired)
        batch["running"] = bool(desired)
    elif desired:
        st.session_state.map_batch = {"pending": desired, "total": len(desired), "done": 0, "running": True, "cancelled": False, "stopped": "", "failures": batch.get("failures", []) if batch else []}


def resume_batch() -> None:
    batch = st.session_state.map_batch
    if batch and batch["pending"]:
        for item in batch["pending"]:
            st.session_state.map_auto_attempted.discard(_geo_key(item["address"], item["city"]))
        batch.update(running=True, cancelled=False, stopped="")
        st.session_state.map_error = ""
        st.session_state.map_auto_next_at = 0.0


def retry_failed_batch() -> None:
    """只在用户明确操作时重查失败地址，成功地址继续使用缓存。"""
    batch = st.session_state.map_batch
    if not batch or not batch.get("failures"):
        return
    orders = st.session_state.get("filtered", st.session_state.get("orders", []))
    eligible = {_geo_key(o.address, current_city()) for o in orders
                if o.address and o.mode != "线上" and o.match_status in {"明确匹配", "可能匹配"}}
    pending = [{"address": item["address"], "city": item["city"]} for item in batch["failures"]
               if _geo_key(item["address"], item["city"]) in eligible]
    if pending:
        st.session_state.map_batch = {"pending": pending, "total": len(pending), "done": 0,
                                      "running": False, "cancelled": False, "stopped": "", "failures": []}
        resume_batch()


def batch_step() -> None:
    """一个可中断的请求单元；每次片段执行最多查询一个地址。"""
    batch = st.session_state.map_batch
    if not batch or not batch["running"] or not batch["pending"]:
        return
    item = batch["pending"][0]
    st.session_state.setdefault("map_auto_attempted", set()).add(_geo_key(item["address"], item["city"]))
    try:
        try:
            result = _client().locate(item["address"], item["city"])
        except MapAPIError as exc:
            if exc.stop_batch:
                raise
            result = GeoResult(status="failed", message=str(exc), resolver_revision=LOCATION_REVISION)
        _cache_geo(item["address"], item["city"], result)
    except (MapAPIError, store.MapStoreError) as exc:
        batch["running"] = False
        batch["stopped"] = str(exc)
        _error(exc)
        return
    if result.status == "failed":
        batch.setdefault("failures", []).append({**item, "message": result.message})
    batch["pending"].pop(0)
    batch["done"] += 1
    if not batch["pending"]:
        batch["running"] = False


def cancel_batch() -> None:
    batch = st.session_state.map_batch
    if batch:
        batch["running"], batch["cancelled"] = False, True


def batch_diagnostic_note() -> str:
    """保存到地图更新事件，方便从导入日志回查失败接口和错误码。"""
    batch = st.session_state.get("map_batch")
    if not batch:
        return ""
    lines = [f"地图地址进度：{batch['done']}/{batch['total']}"]
    if batch["stopped"]:
        address = batch["pending"][0]["address"] if batch["pending"] else ""
        lines.append(f"暂停：{address} · {batch['stopped']}")
    lines.extend(f"失败：{item['address']} · {item['message']}" for item in batch.get("failures", []))
    return "\n".join(lines)


def _batch_panel(refresh: Callable[[], None]) -> None:
    batch = st.session_state.map_batch
    if not batch:
        return
    st.progress(batch["done"] / max(batch["total"], 1), text=f"已查询 {batch['done']} / {batch['total']} 个地址")
    if batch["running"]:
        st.button("取消后续查询", key="map_cancel_batch", on_click=cancel_batch)
        if time.monotonic() < st.session_state.get("map_auto_next_at", 0.0):
            return
        batch_step()
        st.session_state.map_auto_next_at = time.monotonic() + 0.5
        # 每完成一个地址就刷新地图与距离，后续地址由片段继续处理。
        refresh()
        st.rerun()
    elif batch["cancelled"]:
        st.info("已取消后续查询，已完成的定位保留。当前正在执行的请求不能撤回。")
        st.button("继续自动定位", key="map_resume_batch", on_click=resume_batch)
        if not batch.get("refreshed"):
            batch["refreshed"] = True
            refresh()
            st.rerun()
    elif batch["stopped"]:
        st.warning("查询已暂停：" + batch["stopped"] + " 不会自动重试或购买额度。")
        if batch["pending"]:
            st.caption(f"暂停于第 {batch['done'] + 1} 个地址：{batch['pending'][0]['address']}")
        st.button("重试未完成定位", key="map_retry_batch", on_click=resume_batch)
    elif batch["total"] and batch["done"] == batch["total"]:
        if batch.get("failures"):
            st.warning(f"本次定位已结束：已查询 {batch['done']} 个地址，{len(batch['failures'])} 个失败；其他地址已继续查询。")
        else:
            st.success("本次定位完成。")
    if batch.get("failures"):
        with st.expander(f"定位失败（{len(batch['failures'])}）", expanded=False):
            for failure in batch["failures"]:
                st.caption(f"{failure['address']} · {failure['message']}")
        if not batch["running"] and not batch["stopped"] and not batch["cancelled"]:
            st.button("重试失败地址", key="map_retry_failed", on_click=retry_failed_batch)


@st.fragment(run_every=0.5)
def _running_batch(refresh: Callable[[], None]) -> None:
    _batch_panel(refresh)


def render_toolbar(orders: list, refresh: Callable[[], None]) -> None:
    ensure_state()
    if st.session_state.map_error:
        st.warning(st.session_state.map_error)
    origin = applied_origin()
    if origin:
        st.caption(f"当前结果出发点：{origin.label or '已选位置'}　·　默认城市：{current_city() or '未设置'}")
    settings = st.session_state.map_settings
    if not settings.credentials.web_key:
        st.caption("在“地图设置”填写 Web 服务 Key 后可查询订单地址。筛选和导出可继续使用。")
        return
    if not current_city():
        st.info("请先在地图设置保存默认城市，再点击“应用筛选”，以免同名地点被定位到其他城市。")
        return
    results = [lookup_geo(o) for o in orders]
    area_count = sum(bool(r and getattr(r, "area_point", None)) for r in results)
    located = sum(bool(r and r.status == "located" and not getattr(r, "area_point", None)) for r in results)
    ambiguous = sum(bool(r and r.status == "ambiguous" and not getattr(r, "area_point", None)) for r in results)
    st.caption(f"已定位 {located} 单 · 待确认 {ambiguous} 单 · 未定位 {len(orders) - located - ambiguous - area_count} 单（含线上或无地址订单）" + (f" · 区域约略标记 {area_count} 单" if area_count else ""))
    unknown = [o for o in orders if order_distance(o) is None]
    if unknown:
        with st.expander(f"距离待确认（{len(unknown)}）", expanded=False):
            st.caption("这些订单仍保留在原匹配分组中，暂不显示可靠距离。线上订单不自动落点。")
            for order in unknown:
                result = lookup_geo(order)
                reason = "仅线上订单" if order.mode == "线上" else "缺少可识别地址" if not order.address else result.message if result else "等待自动定位"
                st.caption(f"{order.order_number or order.order_id} · {reason or '地点待确认'} · {order.address or '未提供地址'}")
    batch = st.session_state.map_batch
    if batch and batch["total"] == 0:
        st.caption("当前筛选结果没有需要新查询的地址。")
    if batch and batch["running"]:
        _running_batch(refresh)
    else:
        _batch_panel(refresh)


def render_card_location(order, *, compact: bool = False) -> None:
    ensure_state()
    if compact:
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.caption(distance_label(order))
            st.button("地图定位 ↑", key=f"map_locate_{order.order_id}", on_click=_focus_order, args=(order.order_id,))
    else:
        st.caption("距出发点：" + distance_label(order))
    if order.order_id == st.session_state.map_selected_order:
        st.badge("地图已选中", icon=":material/location_on:")
    result = lookup_geo(order)
    if result and getattr(result, "area_point", None):
        st.caption("约略位置：" + result.area_point.label + "；具体上课地点待确认。")
    elif result and result.status == "ambiguous":
        st.caption("地址有多个候选地点，暂不显示地图落点。")
    if not compact:
        st.button("在地图中定位 ↑", key=f"map_locate_{order.order_id}", on_click=_focus_order, args=(order.order_id,))


def _focus_order(order_id: str) -> None:
    select_order(order_id)


def select_order(order_id: str) -> None:
    if st.session_state.map_selected_order != order_id:
        st.session_state.map_order_candidates = []
        st.session_state.pop("map_picked_point", None)
        st.session_state.map_picking = ""
    st.session_state.map_selected_order = order_id
    st.session_state._map_focus_token = st.session_state.get("_map_focus_token", 0) + 1


def _consume_component(result, orders: list) -> None:
    selected = getattr(result, "selected", None)
    picked = getattr(result, "picked", None)
    if isinstance(selected, dict) and selected.get("event_id") and selected.get("event_id") != st.session_state.get("_map_selected_event"):
        if selected.get("id") in {o.order_id for o in orders}:
            select_order(selected["id"])
            st.session_state._map_selected_event = selected["event_id"]
            st.rerun()
    if isinstance(picked, dict) and picked.get("event_id") and st.session_state.map_picking and picked.get("event_id") != st.session_state.get("_map_picked_event"):
        try:
            city = st.session_state.map_settings.default_city if st.session_state.map_picking == "origin" else current_city()
            point = GeoPoint(lng=picked["lng"], lat=picked["lat"], label="地图选点", source="user", precision="exact", city=city)
        except (ValueError, KeyError, TypeError):
            return
        st.session_state._map_picked_event = picked["event_id"]
        st.session_state.map_picked_point = point
        st.session_state.map_picking = ""
        st.rerun()


def render_map_panel(orders: list) -> None:
    from tutor_screen.map_component import render_map
    settings = st.session_state.map_settings
    if not settings.credentials.js_key or not settings.credentials.security_code:
        st.info("填写高德 JS Key 与安全密钥后，这里显示订单地图。")
        return
    origin = applied_origin()
    points = []
    for number, order in enumerate(orders, 1):
        point = point_for(order)
        if not point:
            continue
        if order.hourly_rate is not None:
            pay = f"¥{order.hourly_rate:g}/小时"
        elif order.hourly_rate_min is not None and order.hourly_rate_max is not None:
            pay = f"¥{order.hourly_rate_min:g}–{order.hourly_rate_max:g}/小时"
        else:
            pay = order.price_text or "报酬待确认"
        points.append({
            "id": order.order_id, "number": order.order_number or order.order_id, "lng": point.lng, "lat": point.lat,
            "status": order.match_status, "subject": "、".join(order.subjects),
            "grade": order.grade or "年级待确认", "pay": pay,
            "distance_label": distance_label(order),
            "precision": point.precision,
            "area_scale": point.area_scale,
            "location_label": point.label if point.precision == "area" else "",
            "raw_text": order.raw_text,
        })
    if st.session_state.map_picking:
        st.info("点击地图选择位置，确认后才会保存。")
    result = render_map(
        points=points, origin=origin.model_dump() if origin else None,
        selected_id=st.session_state.map_selected_order, picking=bool(st.session_state.map_picking),
        js_key=settings.credentials.js_key, security_code=settings.credentials.security_code,
        focus_token=st.session_state.get("_map_focus_token", 0),
        height=600,
    )
    _consume_component(result, orders)


def _confirm_order_point(order, point: GeoPoint) -> None:
    store.save_manual(order.address or "", current_city(), point, order_key=_manual_identity(order))
    # 人工位置覆盖自动位置，但保留自动结果，便于恢复且不消耗新请求。
    st.session_state._map_results_dirty = True
    st.session_state.map_picking = ""
    st.session_state.pop("map_picked_point", None)


def _commute_key(origin, dest, mode):
    return (origin.lng, origin.lat, origin.city, dest.lng, dest.lat, dest.city, mode, "amap")


def _inspector_changed() -> None:
    select_order(st.session_state.map_selected_order_picker)


def render_inspector(orders: list, refresh: Callable[[], None]) -> None:
    if not orders:
        return
    ids = [o.order_id for o in orders]
    if st.session_state.map_selected_order not in ids:
        st.session_state.map_selected_order = ""
    options = [""] + ids
    st.session_state.map_selected_order_picker = st.session_state.map_selected_order
    selected = st.selectbox("选择订单查看地图详情", options, key="map_selected_order_picker", format_func=lambda x: x or "选择订单或点击地图标记", on_change=_inspector_changed)
    if not selected:
        return
    order = next(o for o in orders if o.order_id == selected)
    with st.container(border=True):
        st.markdown(f"**订单 {order.order_id}**")
        st.caption(f"原始地址：{order.address or '未提供'} · {distance_label(order)}")
        if order.mode == "线上" or not order.address:
            st.info("线上或缺少地址的订单保留在列表中，不自动构造地图位置。")
            return
        result = lookup_geo(order)
        candidates = st.session_state.map_order_candidates or (result.candidates if result else [])
        with st.expander("确认或修正位置", expanded=not bool(point_for(order))):
            st.text_input("重新搜索订单地点", key="map_order_query", placeholder="输入城市、小区或地铁站", persist_state="session")
            left, right = st.columns(2)
            if left.button("搜索候选地点", key="map_search_order", disabled=not bool(st.session_state.map_settings.credentials.web_key)):
                try:
                    st.session_state.map_order_candidates = _client().search(st.session_state.map_order_query.strip() or order.address, current_city())
                    st.rerun()
                except MapAPIError as exc:
                    _error(exc)
                    st.warning(str(exc))
            if right.button("在地图重新选点", key="map_pick_order"):
                st.session_state.map_picking = f"order:{order.order_id}"
                st.session_state.pop("map_picked_point", None)
                st.rerun()
            if candidates:
                idx = st.selectbox("候选地点", range(len(candidates)), format_func=lambda i: f"{candidates[i].label} · {candidates[i].city} · {candidates[i].lng:.5f}, {candidates[i].lat:.5f}", key=f"map_order_candidate_{selected}")
                if st.button("确认订单位置", key="map_confirm_order"):
                    try:
                        _confirm_order_point(order, candidates[idx])
                        st.session_state.map_order_candidates = []
                        refresh()
                        st.rerun()
                    except store.MapStoreError as exc:
                        st.error(str(exc))
            picked = st.session_state.get("map_picked_point")
            if picked:
                st.caption(f"选中位置：{picked.lng:.6f}, {picked.lat:.6f}")
                if st.button("保存地图选点", key="map_confirm_picked_order"):
                    try:
                        _confirm_order_point(order, picked)
                        refresh()
                        st.rerun()
                    except store.MapStoreError as exc:
                        st.error(str(exc))
            if st.button("恢复自动定位", key="map_restore_order"):
                try:
                    store.restore_auto(order.address, current_city(), order_key=_manual_identity(order))
                    st.session_state._map_results_dirty = True
                    st.session_state.map_order_candidates = []
                    st.session_state.pop("map_picked_point", None)
                    refresh()
                    st.rerun()
                except store.MapStoreError as exc:
                    st.error(str(exc))
        origin, dest = applied_origin(), point_for(order)
        st.markdown("**通勤比较**")
        if not origin or not dest:
            st.caption("应用出发点并确认订单位置后，可按需查询通勤。")
            return
        st.selectbox("交通方式", MODES, key="map_commute_mode", persist_state="session")
        mode = st.session_state.map_commute_mode
        if st.button("查询 / 刷新此方式通勤", key="map_commute_query", disabled=not bool(st.session_state.map_settings.credentials.web_key)):
            try:
                value = _client().commute(origin, dest, mode, current_city())
                st.session_state.map_commute_session[_commute_key(origin, dest, mode)] = value
                if getattr(st.session_state.map_settings, "persist_api_cache", False):
                    store.save_commute(origin, dest, value)
            except (MapAPIError, store.MapStoreError) as exc:
                st.warning(str(exc))
        if mode in MODES:
            params = {
                "from": f"{origin.lng:.6f},{origin.lat:.6f},出发点",
                "to": f"{dest.lng:.6f},{dest.lat:.6f},订单地点",
                "mode": {"公共交通": "bus", "骑行": "ride", "步行": "walk", "驾车": "car"}[mode],
                "src": "Tutor_screen", "callnative": "1",
            }
            st.link_button("在高德地图查看路线", "https://uri.amap.com/navigation?" + urlencode(params))
            st.caption("外部路线不会自动回填；骑行跳转需在移动端查看。")
        found = False
        for travel_mode in MODES:
            value = st.session_state.map_commute_session.get(_commute_key(origin, dest, travel_mode))
            if value is None:
                try:
                    value = store.get_commute(origin, dest, travel_mode)
                except store.MapStoreError as exc:
                    _error(exc)
            if value:
                found = True
                st.write(f"{travel_mode} · 约 {value.minutes:g} 分钟 · {value.distance_m / 1000:.1f} 公里")
        st.caption("历史查询结果，仅供参考。刷新仅查询当前交通方式。" if found else "尚无通勤结果，选择一种方式后主动查询。")


def _same_origin_city(default_city: str, point: GeoPoint | None) -> bool:
    """Treat 福州/福州市 as the same city; never guess from a POI name."""
    if point is None or not point.city.strip():
        return True
    return default_city.strip().removesuffix("市") == point.city.strip().removesuffix("市")


def settings_page() -> None:
    ensure_state()
    st.title("地图设置")
    st.caption("配置地图凭据与常用出发点，为附近订单做好准备。")
    settings = st.session_state.map_settings
    if st.session_state.map_error:
        st.warning(st.session_state.map_error)
    for key, value in {
        "map_js_key": settings.credentials.js_key, "map_security_code": settings.credentials.security_code,
        "map_web_key": settings.credentials.web_key, "map_default_city": settings.default_city,
        "map_persist_cache": getattr(settings, "persist_api_cache", False),
    }.items():
        st.session_state.setdefault(key, value)
    with st.container(border=True, key="map_credentials"):
        st.subheader("地图与查询凭据")
        st.caption("高德地图 · 使用自己的 Key，查询按账号权益执行。")
        left, right = st.columns(2)
        left.text_input("地图 JS API Key", type="password", key="map_js_key", persist_state="session")
        right.text_input("JS 安全密钥 securityJsCode", type="password", key="map_security_code", persist_state="session")
        left, right = st.columns(2)
        left.text_input("Web 服务 API Key", type="password", key="map_web_key", persist_state="session")
        right.text_input("默认城市", key="map_default_city", placeholder="例如：温州市", persist_state="session")
        city_feedback = right.empty()
        st.caption("需要分别申请 Web 端（JS API）和 Web 服务两个 Key：前者显示地图，后者查询地点。保存设置不会发起查询。")
        st.markdown("[申请高德凭据](https://console.amap.com/dev/key/app) · [配置说明](https://lbs.amap.com/api/javascript-api-v2/prerequisites)")
        st.checkbox("已确认账号许可允许长期缓存接口结果，启用本机持久缓存", key="map_persist_cache", persist_state="session")
        st.caption("默认仅缓存当前会话的接口结果。高德条款限制未经许可的存储；常用地点与用户确认位置单独保留。")
        if st.button("保存地图设置", key="map_save_settings", type="primary"):
            try:
                saved = store.MapSettings(
                    default_city=st.session_state.map_default_city.strip(), active_origin_id=settings.active_origin_id,
                    credentials=MapCredentials(js_key=st.session_state.map_js_key.strip(), security_code=st.session_state.map_security_code.strip(), web_key=st.session_state.map_web_key.strip()),
                    persist_api_cache=st.session_state.map_persist_cache,
                )
                store.save_settings(saved)
                st.session_state.map_settings = saved
                st.session_state.map_error = ""
                st.success("地图设置已加密保存在本机。切换默认城市后，到筛选结果点击“应用筛选”生效。")
            except (store.MapStoreError, ValueError) as exc:
                _error(exc)
                st.error(st.session_state.map_error)
    with st.container(border=True, key="map_origins"):
        st.subheader("常用出发点")
        st.caption("保存学校、住处等常用地点，在筛选结果中选择本次出发点。")
        left, right = st.columns(2)
        left.text_input("出发点名称", key="map_origin_name", placeholder="例如：学校 / 住处", persist_state="session")
        with right:
            point = render_origin_search()
        city_message = ""
        if not _same_origin_city(st.session_state.map_default_city, point):
            city_message = f"出发点位于{point.city}，请重新填写默认城市，使其与出发点城市一致。"
        elif not _same_origin_city(st.session_state.map_settings.default_city, point):
            city_message = "默认城市已修改，请先保存地图设置。"
        if city_message:
            city_feedback.markdown(f'<div class="origin-city-warning" role="alert">{escape(city_message)}</div>', unsafe_allow_html=True)
        if point and st.button("保存常用出发点", key="map_origin_save", disabled=bool(city_message)):
            try:
                if not st.session_state.map_origin_name.strip():
                    st.warning("请给出发点填写一个名称。")
                elif not st.session_state.map_settings.default_city:
                    st.warning("请先保存默认城市。")
                else:
                    store.save_origin(st.session_state.map_origin_name.strip(), point)
                    st.session_state.map_origins = store.list_origins()
                    st.session_state.map_origin_selected = None
                    st.session_state.pop("map_picked_point", None)
                    st.success("出发点已保存。到筛选结果选择出发点并应用筛选。")
                    st.rerun()
            except store.MapStoreError as exc:
                st.error(str(exc))
        if not st.session_state.map_origins:
            st.caption("还没有常用出发点，输入关键词并选择确切地点后即可保存。")
        for origin in st.session_state.map_origins:
            with st.container(border=True, key=f"map_saved_origin_{origin.id}"):
                left, right = st.columns([4, 1], vertical_alignment="center")
                left.write(_origin_label(origin))
                if right.button("删除", key=f"map_delete_origin_{origin.id}"):
                    try:
                        store.delete_origin(origin.id)
                        st.session_state.map_origins = store.list_origins()
                        if st.session_state.map_applied["origin_id"] == origin.id:
                            st.session_state.map_applied.update(origin_id="", origin=None)
                            st.session_state.map_origin_draft = ""
                        st.session_state._map_results_dirty = True
                        st.rerun()
                    except store.MapStoreError as exc:
                        st.error(str(exc))
    with st.container(border=True, key="map_cache"):
        st.subheader("本机缓存")
        st.caption("清除查询结果后，常用出发点与用户确认位置仍会保留。")
        if st.button("清除接口定位与通勤缓存", key="map_clear_cache"):
            try:
                store.clear_cache()
                st.session_state.map_geo_session = {}
                st.session_state.map_commute_session = {}
                st.session_state.map_batch = None
                st.session_state.map_auto_epoch += 1
                st.session_state._map_results_dirty = True
                st.success("接口缓存已清除，常用出发点和用户确认位置保留。")
            except store.MapStoreError as exc:
                st.error(str(exc))
