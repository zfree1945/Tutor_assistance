"""地图任务、取消和位置覆盖的行为回归；无模型、无网络、无真实本机数据。"""
from types import SimpleNamespace

import pytest

from tutor_screen import map_store, map_ui
from tutor_screen.maps import CommuteResult, GeoPoint, GeoResult, MapAPIError, MapCredentials
from tutor_screen.models import Order


class State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self[key] = value


@pytest.fixture
def state(monkeypatch):
    value = State(
        _maps_loaded=True,
        map_settings=map_store.MapSettings(default_city="温州"),
        map_origins=[],
        map_applied={"origin_id": "", "origin": None, "city": "温州", "sort": "距离由近到远"},
    )
    monkeypatch.setattr(map_ui, "st", SimpleNamespace(session_state=value))
    map_ui.ensure_state()
    return value


def order(number, address="学校附近"):
    return Order(order_id=number, raw_text="合成订单", address=address, mode="线下")


def point(lng=120.7, city="温州"):
    return GeoPoint(lng=lng, lat=28.0, city=city, label="合成地点")


def batch(state, count=3):
    state.map_batch = {"pending": [{"address": f"合成地址{i}", "city": "温州"} for i in range(count)], "total": count, "done": 0, "running": True, "cancelled": False, "stopped": ""}


def test_one_batch_tick_is_one_address_and_never_automatic_retry(state, monkeypatch):
    calls = []
    def locate(address, city):
        calls.append((address, city))
        return GeoResult(status="located", candidates=[point()])
    monkeypatch.setattr(map_ui, "_client", lambda: SimpleNamespace(locate=locate))
    batch(state)
    map_ui.batch_step()
    assert calls == [("合成地址0", "温州")]
    assert state.map_batch["done"] == 1 and len(state.map_batch["pending"]) == 2
    assert state.map_batch["running"]
    assert map_ui.lookup_geo(order("A", "合成地址0")).status == "located"


@pytest.mark.parametrize("quota", [True, False])
def test_error_pauses_remainder_and_later_ticks_make_no_requests(state, monkeypatch, quota):
    calls = []
    def locate(*args):
        calls.append(args)
        raise MapAPIError("合成额度不足" if quota else "合成网络失败", quota_exhausted=quota)
    monkeypatch.setattr(map_ui, "_client", lambda: SimpleNamespace(locate=locate))
    batch(state)
    map_ui.batch_step()
    for _ in range(4):
        map_ui.batch_step()
    assert len(calls) == 1
    assert state.map_batch["done"] == 0 and len(state.map_batch["pending"]) == 3
    assert not state.map_batch["running"] and state.map_batch["stopped"]


def test_eighth_address_failure_does_not_stall_twenty_address_queue(state, monkeypatch):
    from tutor_screen.maps import AmapClient
    import httpx
    values = [order(str(i), f"合成小区{i}") for i in range(20)]
    enable_auto(state, values)
    calls = []
    fail = [True]
    def handler(request):
        address = request.url.params["address"]
        calls.append(address)
        if address == values[7].address and fail[0]:
            return httpx.Response(200, json={"status": "0", "infocode": "10016", "info": "SERVER_IS_BUSY"})
        return httpx.Response(200, json={"status": "1", "geocodes": [{"location": "120.7,28", "level": "门牌号", "city": "温州"}]})
    monkeypatch.setattr(map_ui, "_client", lambda: AmapClient("synthetic", transport=httpx.MockTransport(handler)))
    for _ in range(25):
        map_ui.sync_auto_batch(values)
        map_ui.batch_step()
    assert calls == [value.address for value in values]
    assert state.map_batch["done"] == state.map_batch["total"] == 20
    assert not state.map_batch["running"] and not state.map_batch["stopped"]
    failed = map_ui.lookup_geo(values[7])
    assert failed.status == "failed" and "10016" in failed.message
    assert len(state.map_batch["failures"]) == 1
    note = map_ui.batch_diagnostic_note()
    assert "20/20" in note and "10016" in note and "合成小区7" in note
    assert map_ui.lookup_geo(values[-1]).status == "located"
    fail[0] = False
    map_ui.retry_failed_batch()
    map_ui.sync_auto_batch(values)
    map_ui.batch_step()
    assert calls[-1] == values[7].address and len(calls) == 21
    assert map_ui.lookup_geo(values[7]).status == "located"
    assert state.map_batch["done"] == state.map_batch["total"] == 1
    assert state.map_batch["failures"] == []


def test_retry_failed_addresses_obeys_current_filter(state, monkeypatch):
    values = [order("A", "甲小区"), order("B", "乙小区")]
    enable_auto(state, values)
    monkeypatch.setattr(map_ui, "_client", lambda: SimpleNamespace(locate=lambda *args: GeoResult(status="failed", message="单地址失败", resolver_revision=3)))
    for _ in values:
        map_ui.sync_auto_batch(values)
        map_ui.batch_step()
    state.filtered = [values[1]]
    map_ui.retry_failed_batch()
    assert state.map_batch["pending"] == [{"address": "乙小区", "city": "温州"}]


def test_cancel_preserves_completed_positions_and_skips_unstarted_requests(state, monkeypatch):
    calls = []
    monkeypatch.setattr(map_ui, "_client", lambda: SimpleNamespace(locate=lambda *args: calls.append(args) or GeoResult(status="located", candidates=[point()])))
    batch(state)
    map_ui.batch_step()
    map_ui.cancel_batch()
    map_ui.batch_step()
    assert len(calls) == 1 and state.map_batch["cancelled"]
    assert map_ui.lookup_geo(order("A", "合成地址0")).candidates[0] == point()
    assert map_ui.lookup_geo(order("B", "合成地址1")) is None


def test_batch_deduplicates_addresses_preserves_online_and_does_not_query_during_prepare(state, monkeypatch):
    online = order("ONLINE", "线上")
    online.mode = "线上"
    values = [order("A"), order("B"), online, order("NOADDRESS", None)]
    monkeypatch.setattr(map_ui, "_client", lambda: pytest.fail("preparing must not call API"))
    result = map_ui.prepare_batch(values)
    assert result["total"] == 1 and not result["running"]
    assert result["pending"] == [{"address": "学校附近", "city": "温州"}]


def test_manual_override_and_restore_keep_original_auto_result(state):
    value = order("A")
    automatic, manual = point(120.7), point(120.8)
    map_ui._cache_geo(value.address, "温州", GeoResult(status="located", candidates=[automatic]))
    map_ui._confirm_order_point(value, manual)
    assert map_ui.point_for(value).lng == manual.lng
    map_store.restore_auto(value.address, "温州", order_key=map_ui._manual_identity(value))
    assert map_ui.point_for(value) == automatic
    assert value.address == "学校附近"


def test_switching_selected_order_clears_candidate_and_pick_state(state):
    state.map_selected_order = "A"
    state.map_order_candidates = [point()]
    state.map_picked_point = point(120.9)
    state.map_picking = "order:A"
    map_ui.select_order("B")
    assert state.map_selected_order == "B"
    assert state.map_order_candidates == [] and "map_picked_point" not in state
    assert state.map_picking == ""


def test_manual_confirmation_is_per_order_even_when_ambiguous_address_is_identical(state):
    first, second = order("A", "同名小区"), order("B", "同名小区")
    first.raw_text, second.raw_text = "合成订单A 数学 同名小区", "合成订单B 英语 同名小区"
    automatic = GeoResult(status="ambiguous", candidates=[point(120.7), point(120.8)])
    map_ui._cache_geo("同名小区", "温州", automatic)
    map_ui._confirm_order_point(first, point(120.7))
    assert map_ui.point_for(first).lng == 120.7
    assert map_ui.point_for(second) is None
    map_ui._confirm_order_point(second, point(120.8))
    assert map_ui.point_for(first).lng == 120.7 and map_ui.point_for(second).lng == 120.8
    map_store.restore_auto(first.address, "温州", order_key=map_ui._manual_identity(first))
    assert map_ui.point_for(first) is None and map_ui.point_for(second).lng == 120.8


def test_same_coordinates_in_different_cities_do_not_reuse_transit_key(state):
    assert map_ui._commute_key(point(city="温州"), point(), "公共交通") != map_ui._commute_key(point(city="杭州"), point(), "公共交通")


def test_unsolicited_and_invalid_component_events_are_ignored(state):
    map_ui._consume_component(SimpleNamespace(selected=["bad"], picked={"lng": 120.7, "lat": 28, "event_id": "stale"}), [order("A")])
    assert "map_picked_point" not in state
    state.map_picking = "order:A"
    map_ui._consume_component(SimpleNamespace(selected=None, picked={"lng": float("nan"), "lat": 28, "event_id": "bad"}), [order("A")])
    assert "map_picked_point" not in state


def test_origin_pick_uses_saved_city_and_duplicate_trigger_is_not_replayed(state, monkeypatch):
    state.map_settings.default_city = "杭州市"
    state.map_picking = "origin"
    reruns = []
    monkeypatch.setattr(map_ui.st, "rerun", lambda: reruns.append(True), raising=False)
    event = SimpleNamespace(selected=None, picked={"lng": 120.7, "lat": 28, "event_id": "once"})
    map_ui._consume_component(event, [])
    assert state.map_picked_point.city == "杭州市"
    map_ui._consume_component(event, [])
    assert len(reruns) == 1


def test_malformed_extracted_address_does_not_break_original_filtering(state):
    value = order("A", "地址\n混入说明")
    assert map_ui.filter_orders([value]) == [value]
    assert map_ui.distance_label(value) == "距离未知"


def enable_auto(state, values):
    state.map_settings.credentials = MapCredentials(js_key="js", security_code="security", web_key="web")
    state.orders = values


def test_auto_locates_unique_filtered_addresses_once_and_reuses_cache(state, monkeypatch):
    first, duplicate, excluded = order("A"), order("B"), order("C", "已排除地址")
    excluded.match_status = "已排除"
    enable_auto(state, [first, duplicate, excluded])
    calls = []
    monkeypatch.setattr(map_ui, "_client", lambda: SimpleNamespace(locate=lambda *args: calls.append(args) or GeoResult(status="located", candidates=[point()], resolver_revision=3)))
    map_ui.sync_auto_batch([first, duplicate, excluded])
    assert state.map_batch["running"] and state.map_batch["total"] == 1
    map_ui.batch_step()
    for _ in range(4):
        map_ui.sync_auto_batch([first, duplicate, excluded])
        map_ui.batch_step()
    assert calls == [("学校附近", "温州")]


@pytest.mark.parametrize("cancelled", [False, True])
def test_auto_does_not_resume_cancelled_or_failed_batch_on_rerender(state, monkeypatch, cancelled):
    values = [order("A", "甲小区"), order("B", "乙小区")]
    enable_auto(state, values)
    calls = []
    def locate(*args):
        calls.append(args)
        raise MapAPIError("合成额度不足", quota_exhausted=True)
    monkeypatch.setattr(map_ui, "_client", lambda: SimpleNamespace(locate=locate))
    map_ui.sync_auto_batch(values)
    if cancelled:
        map_ui.cancel_batch()
    else:
        map_ui.batch_step()
    for _ in range(4):
        map_ui.sync_auto_batch(values)
        map_ui.batch_step()
    assert len(calls) == (0 if cancelled else 1)
    assert not state.map_batch["running"]
    if not cancelled:
        map_ui.resume_batch()
        map_ui.sync_auto_batch(values)
        map_ui.batch_step()
        assert len(calls) == 2  # only the explicit retry permits another call


def test_filter_changes_replace_unstarted_addresses_and_do_not_query_excluded_ones(state, monkeypatch):
    values = [order("A", "甲小区"), order("B", "乙小区")]
    enable_auto(state, values)
    calls = []
    monkeypatch.setattr(map_ui, "_client", lambda: SimpleNamespace(locate=lambda *args: calls.append(args) or GeoResult(status="not_found", resolver_revision=3)))
    map_ui.sync_auto_batch(values)
    map_ui.sync_auto_batch([values[1]])
    map_ui.batch_step()
    assert calls == [("乙小区", "温州")]
    map_ui.sync_auto_batch(values)
    map_ui.batch_step()
    assert calls[-1] == ("甲小区", "温州")


def test_old_unresolved_cache_is_upgraded_once_but_new_unresolved_cache_is_not_polled(state, monkeypatch):
    value = order("A")
    enable_auto(state, [value])
    map_ui._cache_geo(value.address, "温州", GeoResult(status="ambiguous", candidates=[point()]))
    calls = []
    monkeypatch.setattr(map_ui, "_client", lambda: SimpleNamespace(locate=lambda *args: calls.append(args) or GeoResult(status="ambiguous", candidates=[point()], resolver_revision=3)))
    map_ui.sync_auto_batch([value])
    map_ui.batch_step()
    map_ui.sync_auto_batch([value])
    map_ui.batch_step()
    assert len(calls) == 1


def test_running_session_can_upgrade_old_result_objects_without_revision_field(state):
    value = order("A")
    enable_auto(state, [value])
    # Live Streamlit sessions can retain an instance of the old model class
    # even after source reload, so disk JSON backward compatibility is not enough.
    state.map_geo_session[map_ui._geo_key(value.address, "温州")] = SimpleNamespace(status="ambiguous", candidates=[point()], message="旧版候选")
    map_ui.sync_auto_batch([value])
    assert state.map_batch["running"]
    assert state.map_batch["pending"] == [{"address":value.address, "city":"温州"}]
