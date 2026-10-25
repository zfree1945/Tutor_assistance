"""高德请求契约、距离精度和独立地图本机存储；完全离线。"""
import json
import math
import sqlite3

import httpx
import pytest
from pydantic import ValidationError

from tutor_screen import map_store
from tutor_screen.map_store import MapSettings
from tutor_screen.maps import (
    AmapClient, CommuteResult, GeoPoint, GeoResult, MapAPIError,
    MapCredentials, clean_query, distance_km,
)


@pytest.fixture(autouse=True)
def isolated_maps(monkeypatch, tmp_path):
    monkeypatch.setattr(map_store, "MAP_PATH", tmp_path / "map_data.sqlite3")
    yield
    map_store._MEMORY.clear()


def point(lng=116.397499, lat=39.908722, **kwargs):
    return GeoPoint(lng=lng, lat=lat, **kwargs)


def mock_client(payload, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, json=payload)
    return AmapClient("offline-test-key", transport=httpx.MockTransport(handler))


def geocode(location="116.397499,39.908722", level="住宅区", name="北京市示例小区", city="北京市"):
    return {"formatted_address": name, "location": location, "level": level, "city": city}


def test_local_distance_same_nearby_antipodal_and_symmetry():
    origin = point(0, 0)
    assert distance_km(origin, origin) == 0
    assert distance_km(origin, point(0, 1)) == pytest.approx(111.19508, abs=.001)
    assert distance_km(origin, point(180, 0)) == pytest.approx(math.pi * 6371.0088)
    assert distance_km(point(), point(117, 40)) == pytest.approx(distance_km(point(117, 40), point()))


def test_legacy_geo_json_without_area_scale_is_read_without_cache_changes():
    legacy = '{"status":"ambiguous","candidates":[{"lng":120.7,"lat":28,"precision":"approximate"}],"area_point":{"lng":120.7,"lat":28,"precision":"area"},"resolver_revision":3}'
    map_store.save_settings(MapSettings(persist_api_cache=True))
    key = map_store._geo_key("鹿城区人民路", "温州市")
    with map_store._database() as connection, connection:
        connection.execute("INSERT INTO geocache VALUES (?, ?)", (key, legacy))
    map_store._MEMORY.clear()
    result = map_store.get_geo("鹿城区人民路", "温州市")
    assert result.area_point.area_scale is None
    assert result.candidates[0].area_scale is None
    assert result.resolver_revision == 3
    with map_store._database() as connection:
        assert connection.execute("SELECT value FROM geocache WHERE id=?", (key,)).fetchone()["value"] == legacy
    map_store._memory()["geo"][key] = json.loads(legacy)
    assert map_store.get_geo("鹿城区人民路", "温州市") == result


@pytest.mark.parametrize("kwargs", [{"lng": float("nan")}, {"lat": float("inf")}, {"lng": 181}, {"lat": -91}, {"coordinate_system": "BD-09"}, {"coordinate_system": "WGS-84"}])
def test_points_reject_invalid_or_mixed_coordinates(kwargs):
    values = {"lng": 116.4, "lat": 39.9, **kwargs}
    with pytest.raises(ValidationError):
        GeoPoint(**values)


def test_distance_revalidates_unchecked_copy():
    with pytest.raises(ValidationError):
        distance_km(point().model_copy(update={"lat": float("nan")}), point())


def test_locate_community_is_approximate_only_minimal_address_sent():
    requests = []
    client = mock_client({"status": "1", "infocode": "10000", "geocodes": [geocode()]}, requests)
    result = client.locate("【地址】示例小区，联系电话：13800138000，报酬200元/小时", "北京")
    assert result.status == "located"
    assert result.candidates[0].precision == "approximate"
    assert result.candidates[0].city == "北京市"
    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "restapi.amap.com"
    assert request.url.scheme == "https"
    assert request.url.path == "/v3/geocode/geo"
    assert dict(request.url.params) == {"address": "示例小区", "city": "北京", "key": "offline-test-key"}
    assert "13800138000" not in str(request.url)


@pytest.mark.parametrize("level", ["门牌号", "单元号", "门址"])
def test_geocode_detailed_level_can_be_exact(level):
    result = mock_client({"status": "1", "geocodes": [geocode(level=level)]}).locate("示例地址", "北京")
    assert result.status == "located"
    assert result.candidates[0].precision == "exact"


@pytest.mark.parametrize("level", ["区县", "道路", "市", "未知", ""])
def test_single_coarse_geocode_still_requires_confirmation(level):
    result = mock_client({"status": "1", "geocodes": [geocode(level=level)]}).locate("示例地址", "北京")
    assert result.status == "ambiguous"


def test_multiple_candidates_are_never_arbitrarily_selected():
    result = mock_client({"status": "1", "geocodes": [geocode(), geocode("117,40", name="另一个小区")]}).locate("示例小区", "北京")
    assert result.status == "ambiguous"
    assert len(result.candidates) == 2


def test_single_unexpected_city_requires_confirmation_but_explicit_other_city_allowed():
    client = mock_client({"status": "1", "geocodes": [geocode(city="上海市", name="上海市示例小区")]})
    assert client.locate("示例小区", "北京").status == "ambiguous"
    assert client.locate("上海市示例小区", "北京").status == "located"


def test_invalid_coordinates_are_not_map_points():
    client = mock_client({"status": "1", "geocodes": [geocode("nan,40"), geocode("116,91")], "pois": []})
    assert client.locate("示例小区", "北京").status == "not_found"


def test_geocode_falls_back_to_unique_verified_poi_as_approximate_location():
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/v3/geocode/geo":
            return httpx.Response(200, json={"status": "1", "geocodes": []})
        return httpx.Response(200, json={"status": "1", "pois": [{"name": "同名小区", "location": "116,40", "cityname": "北京市", "address": []}]})
    result = AmapClient("test", transport=httpx.MockTransport(handler)).locate("同名小区", "北京")
    assert result.status == "located"
    assert result.candidates[0].precision == "approximate"
    assert len(requests) == 2
    assert requests[-1].url.path == "/v3/place/text"
    assert requests[-1].url.params["offset"] == "10"
    assert result.candidates[0].label == "同名小区 · 北京市"


@pytest.mark.parametrize("poi_name,poi_city,poi_district,poi_address,expected", [
    ("九宸园", "温州市", "鹿城区", "某路", "located"),
    ("其他花园", "温州市", "鹿城区", "某路", "ambiguous"),
    ("九宸园", "杭州市", "鹿城区", "某路", "ambiguous"),
    ("九宸园", "温州市", "瓯海区", "某路", "ambiguous"),
])
def test_coarse_geocode_uses_poi_only_when_name_city_and_district_agree(poi_name, poi_city, poi_district, poi_address, expected):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("geocode/geo"):
            return httpx.Response(200, json={"status":"1","geocodes":[geocode("120.7,28", level="乡镇", name="温州市鹿城区滨江街道", city="温州市")]})
        return httpx.Response(200, json={"status":"1","pois":[{"name":poi_name,"cityname":poi_city,"adname":poi_district,"address":poi_address,"location":"120.71,28.01"}]})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate("温州市鹿城区襟江路华润置地九宸园", "温州市")
    assert result.status == expected and result.resolver_revision == 3
    assert len(calls) == 2
    assert calls[1].url.params["keywords"] == "鹿城区华润置地九宸园"


@pytest.mark.parametrize("road,expected", [("人民路", "located"), ("学院路", "ambiguous")])
def test_chain_landmark_must_match_original_road(road, expected):
    def handler(request):
        if request.url.path.endswith("geocode/geo"):
            return httpx.Response(200, json={"status":"1","geocodes":[]})
        return httpx.Response(200, json={"status":"1","pois":[{"name":"肯德基(测试店)","cityname":"温州市","adname":"鹿城区","address":road,"location":"120.7,28"}]})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate("鹿城区人民路肯德基对面", "温州市")
    assert result.status == expected


def test_search_missing_or_list_values_do_not_become_strings():
    result = mock_client({"status": "1", "pois": [{"name": "学校", "location": "116.3,39.8", "address": [], "cityname": [], "adname": []}]}).search("学校", "北京")
    assert result[0].label == "学校"
    assert result[0].city == "北京"


@pytest.mark.parametrize("code", ["10003", "10004", "10010", "10014", "10015", "10019", "10020", "10021", "10029", "10044", "10045", "40000", "40002", "40003"])
def test_quota_or_frequency_stops_and_does_not_retry(code):
    requests = []
    client = mock_client({"status": "0", "infocode": code, "info": "secret=offline-test-key"}, requests)
    with pytest.raises(MapAPIError) as raised:
        client.search("地点", "北京")
    assert raised.value.quota_exhausted is True
    assert len(requests) == 1
    assert "offline-test-key" not in str(raised.value)


@pytest.mark.parametrize("payload", [{"status": "0", "infocode": "10001", "info": "private"}, {"foo": "private"}, [], {"status": "1", "pois": {}}])
def test_errors_are_sanitized(payload):
    with pytest.raises(MapAPIError) as raised:
        mock_client(payload).search("地点", "北京")
    assert "private" not in str(raised.value)
    assert "offline-test-key" not in str(raised.value)


@pytest.mark.parametrize("status_code", [302, 429, 500])
def test_http_failure_and_redirect_never_follow_or_leak(status_code):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status_code, headers={"Location": "https://evil.example/private"}, text="private offline-test-key")
    with pytest.raises(MapAPIError) as raised:
        AmapClient("offline-test-key", transport=httpx.MockTransport(handler)).search("地点")
    assert len(requests) == 1
    assert raised.value.quota_exhausted == (status_code == 429)
    assert "private" not in str(raised.value)


def test_timeout_has_no_url_or_key_in_error():
    def handler(request):
        raise httpx.ConnectTimeout("https://restapi.amap.com?key=private", request=request)
    with pytest.raises(MapAPIError) as raised:
        AmapClient("private", transport=httpx.MockTransport(handler)).search("地点")
    assert "private" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("code", ["10016", "20000", "30001", "99999"])
def test_single_address_errors_keep_safe_code_and_endpoint(code):
    with pytest.raises(MapAPIError) as raised:
        mock_client({"status": "0", "infocode": code, "info": "private key=offline-test-key"}).locate("合成小区", "温州")
    error = raised.value
    assert not error.stop_batch and not error.quota_exhausted
    assert error.code == code and error.endpoint == "geocode"
    assert code in str(error) and "地址解析" in str(error)
    assert "private" not in str(error) and "offline-test-key" not in str(error)


@pytest.mark.parametrize("code", ["10001", "10003", "10021"])
def test_account_and_limit_errors_still_stop_the_whole_queue(code):
    with pytest.raises(MapAPIError) as raised:
        mock_client({"status": "0", "infocode": code}).locate("合成小区", "温州")
    assert raised.value.stop_batch and raised.value.code == code


def test_unknown_response_cannot_inject_untrusted_error_code():
    with pytest.raises(MapAPIError) as raised:
        mock_client({"status": "0", "infocode": "private=offline-test-key"}).search("合成地点")
    assert raised.value.stop_batch and raised.value.code == ""
    assert "offline-test-key" not in str(raised.value)


def test_real_request_pacing_covers_geocode_and_supplemental_search(monkeypatch):
    from tutor_screen import maps
    now = [10.0]
    waits = []
    monkeypatch.setattr(maps, "_NEXT_REQUEST_AT", 0.0)
    monkeypatch.setattr(maps.time, "monotonic", lambda: now[0])
    def sleep(delay):
        waits.append(delay)
        now[0] += delay
    monkeypatch.setattr(maps.time, "sleep", sleep)
    starts = []
    def send(transport, request):
        starts.append((now[0], request.url.path))
        return httpx.Response(200, json={"status": "1", "geocodes": [], "pois": []})
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send)
    client = AmapClient("synthetic")
    client.locate("合成地点", "温州")
    AmapClient("synthetic").search("另一个地点", "温州")
    assert [when for when, _ in starts] == [10.0, 11.0, 12.0]
    assert waits == [1.0, 1.0]


@pytest.mark.parametrize("mode,path", [("步行", "/v3/direction/walking"), ("驾车", "/v3/direction/driving"), ("骑行", "/v4/direction/bicycling"), ("公共交通", "/v3/direction/transit/integrated")])
def test_each_route_uses_correct_endpoint_and_seconds_to_minutes(mode, path):
    requests = []
    paths = [{"duration": "600", "distance": "1000"}, {"duration": "300", "distance": "1200"}]
    payload = {"status": "1", "route": {"transits" if mode == "公共交通" else "paths": paths}}
    if mode == "骑行":
        payload = {"errcode": 0, "data": {"paths": paths}}
    result = mock_client(payload, requests).commute(point(city="北京市"), point(117, 40, city="天津市"), mode)
    assert result.minutes == 5
    assert result.distance_m == 1200
    assert result.mode == mode
    assert requests[0].url.path == path
    params = dict(requests[0].url.params)
    assert params["origin"] == "116.397499,39.908722"
    assert params["destination"] == "117.000000,40.000000"
    assert "date" not in params and "time" not in params
    if mode == "公共交通":
        assert params["city"] == "北京市" and params["cityd"] == "天津市"


def test_transit_missing_city_rejected_without_network():
    requests = []
    with pytest.raises(MapAPIError):
        mock_client({}, requests).commute(point(), point(117, 40), "公共交通")
    assert not requests


@pytest.mark.parametrize("paths", [[], [{"duration": "nan", "distance": "1"}], [{"duration": "30"}], [{"duration": "-10", "distance": "1"}]])
def test_bad_route_does_not_fake_duration(paths):
    with pytest.raises(MapAPIError):
        mock_client({"status": "1", "route": {"paths": paths}}).commute(point(), point(117, 40), "驾车")


def test_unsupported_mode_no_requests():
    requests = []
    with pytest.raises(MapAPIError, match="不支持"):
        mock_client({}, requests).commute(point(), point(), "飞行")
    assert not requests


@pytest.mark.parametrize("query", ["整条订单\n地址：示例小区", "a" * 161, "电话：13800138000", ""])
def test_invalid_queries_never_send_network(query):
    requests = []
    with pytest.raises(MapAPIError):
        mock_client({}, requests).search(query)
    assert not requests


@pytest.mark.parametrize("query", ["示例小区 微信abc123", "示例小区，薪资：200元/小时", "示例小区 13800138000", "【地址】示例小区，科目：数学", "示例小区 200元/小时"])
def test_clean_query_excludes_unrelated_data(query):
    assert clean_query(query) == "示例小区"


def test_store_credentials_only_encrypted_and_other_settings_plain():
    credentials = MapCredentials(js_key="test-js-secret", security_code="test-code-secret", web_key="test-web-secret")
    map_store.save_settings(MapSettings(default_city="北京", credentials=credentials))
    saved = map_store.load_settings()
    assert saved.credentials == credentials
    assert saved.default_city == "北京"
    content = map_store.MAP_PATH.read_bytes()
    assert all(value.encode() not in content for value in credentials.model_dump().values())
    assert "test-web-secret" not in repr(credentials)
    assert "test-web-secret" not in repr(saved)


def test_store_default_is_session_cache_not_provider_disk_archive():
    result = GeoResult(status="located", candidates=[point(label="private-query-result")])
    map_store.save_geo("示例小区", "北京", result)
    assert map_store.get_geo("示例小区", "北京") == result
    assert b"private-query-result" not in map_store.MAP_PATH.read_bytes()
    map_store._MEMORY.clear()
    assert map_store.get_geo("示例小区", "北京") is None


def test_store_authorized_cache_survives_reload_and_city_isolation():
    map_store.save_settings(MapSettings(persist_api_cache=True))
    result = GeoResult(status="located", candidates=[point()])
    map_store.save_geo("示例小区", "北京", result)
    map_store._MEMORY.clear()
    assert map_store.get_geo("示例小区", "北京") == result
    assert map_store.get_geo("示例小区", "上海") is None
    assert "示例小区".encode() not in map_store.MAP_PATH.read_bytes()


def test_store_manual_correction_and_restore_auto():
    automatic = GeoResult(status="located", candidates=[point()])
    manual = point(117, 40, source="user", label="手选位置")
    map_store.save_geo("地址", "北京", automatic)
    map_store.save_manual("地址", "北京", manual)
    assert map_store.resolve_geo("地址", "北京").candidates[0] == manual
    assert map_store.get_geo("地址", "北京") == automatic
    map_store.restore_auto("地址", "北京")
    assert map_store.resolve_geo("地址", "北京") == automatic


def test_clear_cache_preserves_origins_user_positions_settings():
    map_store.save_settings(MapSettings(default_city="北京", persist_api_cache=True))
    origin = map_store.save_origin("家", point(source="user"))
    map_store.save_manual("地址", "北京", point(117, 40, source="user"))
    map_store.save_geo("另一个地址", "北京", GeoResult(status="not_found"))
    result = CommuteResult(mode="步行", minutes=10, distance_m=700)
    map_store.save_commute(origin.point, point(117, 40), result)
    map_store.clear_cache()
    assert map_store.get_geo("另一个地址", "北京") is None
    assert map_store.get_commute(origin.point, point(117, 40), "步行") is None
    assert map_store.list_origins() == [origin]
    assert map_store.resolve_geo("地址", "北京").status == "located"
    assert map_store.load_settings().default_city == "北京"
    assert map_store.stats() == {"origins": 1, "manual": 1, "geo": 0, "commutes": 0}


def test_origins_edit_delete_clears_active_only():
    first = map_store.save_origin("学校", point(source="user"))
    second = map_store.save_origin("住处", point(117, 40, source="user"))
    map_store.save_settings(MapSettings(active_origin_id=first.id))
    edited = map_store.save_origin("新校区", point(118, 41, source="user"), first.id)
    assert [item.name for item in map_store.list_origins()] == ["新校区", "住处"]
    assert edited.id == first.id
    map_store.delete_origin(first.id)
    assert map_store.load_settings().active_origin_id == ""
    assert map_store.list_origins() == [second]


def test_different_commute_modes_and_changed_points_never_reuse_result():
    origin, destination = point(city="北京"), point(117, 40)
    bus = CommuteResult(mode="公共交通", minutes=45, distance_m=10000)
    bike = CommuteResult(mode="骑行", minutes=28, distance_m=9000)
    map_store.save_commute(origin, destination, bus)
    map_store.save_commute(origin, destination, bike)
    assert map_store.get_commute(origin, destination, "公共交通") == bus
    assert map_store.get_commute(origin, destination, "骑行") == bike
    assert map_store.get_commute(origin, point(117.1, 40), "公共交通") is None
    assert map_store.get_commute(point(116.5, 39.9), destination, "公共交通") is None
    assert map_store.get_commute(origin.model_copy(update={"city": "天津"}), destination, "公共交通") is None
    assert map_store.get_commute(origin.model_copy(update={"label": "renamed"}), destination, "公共交通") == bus


def test_revoking_persistent_cache_removes_disk_results():
    map_store.save_settings(MapSettings(persist_api_cache=True))
    map_store.save_geo("地址", "北京", GeoResult(status="not_found"))
    map_store.save_commute(point(), point(117, 40), CommuteResult(mode="骑行", minutes=28, distance_m=9000))
    map_store.save_settings(MapSettings(persist_api_cache=False))
    map_store._MEMORY.clear()
    assert map_store.get_geo("地址", "北京") is None
    assert map_store.get_commute(point(), point(117, 40), "骑行") is None


def test_store_corrupt_database_error_is_safe():
    map_store.MAP_PATH.write_bytes(b"private-credentials-not-a-database")
    with pytest.raises(map_store.MapStoreError) as raised:
        map_store.load_settings()
    assert "private" not in str(raised.value)


def test_store_future_schema_preserved_and_rejected():
    with sqlite3.connect(map_store.MAP_PATH) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(map_store.MapStoreError, match="版本"):
        map_store.load_settings()


@pytest.mark.parametrize("address", ["地址第一行\n地址第二行", "微信：private123", "a" * 161])
def test_store_invalid_extracted_address_is_safe_store_error(address):
    # lookup_geo 的调用者只需捕获本机存储错误，订单不会因底层查询校验而崩溃。
    with pytest.raises(map_store.MapStoreError, match="地点格式"):
        map_store.get_manual(address, "北京")
    with pytest.raises(map_store.MapStoreError, match="地点格式"):
        map_store.get_geo(address, "北京")


def test_store_invalid_city_is_safe_settings_error():
    with pytest.raises(map_store.MapStoreError, match="默认城市"):
        map_store.save_settings(MapSettings(default_city="联系：13800138000\n北京"))
