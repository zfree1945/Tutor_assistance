"""POI 请求范围、结构化核验和缓存兼容性；不连接真实高德服务。"""
import httpx
import pytest
from unittest.mock import patch

from tutor_screen import map_store
from tutor_screen.map_store import MapSettings
from tutor_screen.maps import AmapClient, GeoPoint, GeoResult, MapAPIError
from tutor_screen.maps import LOCATION_REVISION, _same_city
from tutor_screen.models import Order


def poi(**changes):
    return {"name": "保利合成住宅", "cityname": "福州市", "adname": "晋安区", "adcode": "350111",
            "id": "synthetic-poi", "location": "119.3,26.1", "address": "合成路", **changes}


def region(**changes):
    return {"formatted_address": "福建省福州市晋安区", "city": "福州市", "district": "晋安区",
            "adcode": "350111", "level": "区县", "location": "119.4,26.2", **changes}


def service(pois=None, initial=None, regions=None):
    calls = []
    def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("place/text"):
            payload = {"pois": [poi()] if pois is None else pois}
        else:
            payload = {"geocodes": (regions if regions is not None else [region()]) if request.url.params["address"] == "晋安区" else (initial or [])}
        return httpx.Response(200, json={"status": "1", **payload})
    return AmapClient("synthetic", transport=httpx.MockTransport(handler)), calls


def test_district_code_is_resolved_once_and_all_poi_fields_are_retained():
    client, calls = service()
    first = client.search("福州晋安区保利合成住宅附近", "温州")
    second = client.search("福州晋安区合成雅苑", "温州")
    assert len(calls) == 3
    assert calls[0][1]["address"] == "晋安区" and calls[0][1]["city"] == "福州市"
    assert calls[1][1]["city"] == calls[2][1]["city"] == "350111"
    assert calls[1][1]["keywords"] == "保利合成住宅"
    assert calls[1][1]["citylimit"] == "true" and calls[1][1]["extensions"] == "all"
    assert (first[0].city, first[0].district, first[0].adcode, first[0].poi_id, first[0].name) == (
        "福州市", "晋安区", "350111", "synthetic-poi", "保利合成住宅")
    assert first == second


@pytest.mark.parametrize("query,hint,scope,limited", [
    ("福州保利合成住宅", "温州", "福州市", "true"),
    ("保利合成住宅", "福州", "福州", "false"),
    ("保利合成住宅", "", None, "false"),
])
def test_explicit_city_limits_search_and_missing_city_is_still_searchable(query, hint, scope, limited):
    client, calls = service()
    client.search(query, hint)
    assert len(calls) == 1
    assert calls[0][1].get("city") == scope and calls[0][1]["citylimit"] == limited
    assert calls[0][1]["keywords"] == "保利合成住宅"


def test_verified_geocode_adcode_is_reused_for_poi_scope_without_extra_request():
    client, calls = service(initial=[region()])
    result = client.locate("福州晋安区保利合成住宅")
    assert result.status == "located" and len(calls) == 2
    assert calls[1][1]["city"] == "350111"


@pytest.mark.parametrize("changes", [
    {"cityname": "杭州市"},
    {"adname": "仓山区"},
    {"adcode": "350104"},
    {"name": "保利合成住宅售楼部"},
    {"name": "保利合成住宅物业服务中心"},
    {"name": "和光尘越便利店"},
    {"name": "光尘越"},
    {"name": "尘越"},
    {"cityname": [], "adname": [], "adcode": []},
    {"cityname": "福州市", "adname": [], "adcode": []},
])
def test_wrong_or_insufficient_poi_evidence_does_not_become_confirmed_location(changes):
    client, _ = service(pois=[poi(**changes)])
    result = client.locate("福州晋安区保利合成住宅")
    assert result.status == "ambiguous"
    assert result.candidates  # 失败候选仍可供查看和人工处理。
    assert not result.area_point or result.area_point.label != "保利合成住宅"


def test_matching_code_can_verify_missing_district_but_does_not_override_conflicting_district():
    client, _ = service(pois=[poi(adname=[])])
    assert client.locate("福州晋安区保利合成住宅").status == "located"
    client, _ = service(pois=[poi(adname="仓山区")])
    assert client.locate("福州晋安区保利合成住宅").status == "ambiguous"


@pytest.mark.parametrize("changes", [{"city": "杭州市"}, {"district": "仓山区"}, {"adcode": "invalid"}, {"adcode": "350100"}])
def test_unverified_region_code_is_not_used_as_search_scope(changes):
    client, calls = service(regions=[region(**changes)])
    client.locate("福州晋安区保利合成住宅")
    search = next(params for path, params in calls if path.endswith("place/text"))
    assert search["city"] == "福州市" and search["citylimit"] == "true"


def test_conflicting_region_codes_do_not_select_first_code():
    client, calls = service(regions=[region(), region(adcode="350104")])
    client.locate("福州晋安区保利合成住宅")
    search = next(params for path, params in calls if path.endswith("place/text"))
    assert search["city"] == "福州市"


def test_distinct_poi_ids_are_kept_and_dispersed_places_are_not_automatically_chosen():
    client, _ = service(pois=[poi(id="one"), poi(id="two", location="119.8,26.7")])
    result = client.locate("福州晋安区保利合成住宅")
    assert result.status == "ambiguous"
    assert {p.poi_id for p in result.candidates} == {"one", "two"}
    assert not result.area_point or result.area_point.label != "保利合成住宅"
    client, _ = service(pois=[poi(id="one"), poi(id="two")])
    assert len(client.search("保利合成住宅")) == 2


def test_wrong_district_single_detailed_geocode_is_checked_before_acceptance():
    client, _ = service(initial=[region(district="仓山区", adcode="350104", level="门牌号",
                                       formatted_address="福州市仓山区保利合成住宅")], pois=[])
    assert client.locate("福州晋安区保利合成住宅").status != "located"


def test_metadata_roundtrips_cache_and_old_points_still_load(monkeypatch, tmp_path):
    monkeypatch.setattr(map_store, "MAP_PATH", tmp_path / "map.sqlite3")
    map_store.save_settings(MapSettings(persist_api_cache=True))
    client, _ = service()
    result = client.locate("福州晋安区保利合成住宅")
    map_store.save_geo("福州晋安区保利合成住宅", "福州", result)
    map_store._MEMORY.clear()
    restored = map_store.get_geo("福州晋安区保利合成住宅", "福州")
    assert restored == result
    assert restored.candidates[0].poi_id == "synthetic-poi"
    old = GeoPoint.model_validate({"lng": 119.3, "lat": 26.1, "label": "旧位置", "city": "福州"})
    assert old.district == old.adcode == old.poi_id == old.name == ""
    assert GeoResult.model_validate_json(result.model_dump_json()) == result


def test_limit_during_region_lookup_stops_before_poi_request():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "0", "infocode": "10003"})
    with pytest.raises(MapAPIError) as error:
        AmapClient("synthetic", transport=httpx.MockTransport(handler)).search("福州晋安区保利合成住宅")
    assert error.value.stop_batch and error.value.quota_exhausted and len(calls) == 1


@pytest.mark.parametrize("manual,revision,pending", [(False, 5, True), (True, 5, False), (False, LOCATION_REVISION, False)])
def test_old_automatic_location_is_rechecked_once_but_manual_location_is_preserved(manual, revision, pending):
    from tutor_screen.map_ui import prepare_batch
    cached = GeoResult(status="located", resolver_revision=revision,
                       candidates=[GeoPoint(lng=119.3, lat=26.1, source="user" if manual else "amap")])
    order = Order(order_id="regression", raw_text="福州晋安区保利合成住宅", address="福州晋安区保利合成住宅")
    with patch("tutor_screen.map_ui.lookup_geo", return_value=cached), patch("tutor_screen.map_ui.current_city", return_value="福州"):
        assert bool(prepare_batch([order])["pending"]) is pending


@pytest.mark.parametrize("hint,code,city_code,expected", [
    ("350100", "350111", "0591", True),
    ("350111", "350104", "0591", False),
    ("110000", "110101", "010", True),
    ("0591", "350111", "0591", True),
    ("010", "350111", "0591", False),
    ("010", "350111", "", False),
])
def test_numeric_city_scope_is_checked_against_returned_codes(hint, code, city_code, expected):
    point = GeoPoint(lng=119.3, lat=26.1, city="福州市", adcode=code, city_code=city_code)
    assert _same_city(point, "保利合成住宅", hint) is expected


def test_search_preserves_returned_citycode_for_numeric_scope_verification():
    client, _ = service(pois=[poi(citycode="0591")])
    assert client.search("保利合成住宅", "0591")[0].city_code == "0591"
