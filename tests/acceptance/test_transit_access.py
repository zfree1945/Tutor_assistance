"""Transit access fallback preserves original addresses and location precision."""
import httpx
import pytest

from tutor_screen.maps import AmapClient, GeoPoint, LOCATION_REVISION, MapAPIError, split_address, _matches_landmark


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr(AmapClient, "_pace_request", lambda self: None)


def client_for(original, *, error="30001", empty=False, fallback_city="福州市", fallback_error="", exact=False):
    calls = []

    def handler(request):
        params = dict(request.url.params)
        calls.append((request.url.path, params))
        if request.url.path.endswith("place/text"):
            return httpx.Response(200, json={"status": "1", "infocode": "10000", "pois": []})
        query = params["address"]
        if not exact and query == split_address(original, "福州市").query:
            if not empty:
                return httpx.Response(200, json={"status": "0", "infocode": error})
            codes = []
        elif fallback_error:
            return httpx.Response(200, json={"status": "0", "infocode": fallback_error})
        else:
            codes = [{"formatted_address": query, "location": "119.3,26.1", "level": "兴趣点",
                      "city": fallback_city}]
        return httpx.Response(200, json={"status": "1", "infocode": "10000", "geocodes": codes})

    return AmapClient("synthetic", transport=httpx.MockTransport(handler)), calls


@pytest.mark.parametrize("access", ["出口", "入口", "出入口", "A口", "B口", "A1口", "B2口", "A出口", "B入口", "1号出口", "(A口)", "（B出口）"])
def test_access_engine_error_falls_back_once_to_station_reference(access):
    station = "地铁5号线吴山地铁"
    address = station + access
    client, calls = client_for(address)
    result = client.locate(address, "福州市")
    assert result.status == "ambiguous"
    assert result.area_point and result.area_point.precision == "area"
    assert result.area_point.area_scale == "small"
    assert result.area_point.lng == 119.3
    assert "尚未确认具体入口或出口" in result.message
    assert result.resolver_revision == LOCATION_REVISION
    assert [params["address"] for path, params in calls if "geocode" in path] == [split_address(address, "福州市").query, station]
    assert all(params["city"] == "福州市" for _, params in calls)


@pytest.mark.parametrize("access", ["入口", "出口", "出入口", "A口", "B口"])
def test_empty_access_result_also_tries_station(access):
    address = "吴山地铁站" + access
    client, calls = client_for(address, empty=True)
    result = client.locate(address, "福州市")
    assert result.area_point is not None
    assert result.status == "ambiguous"
    assert [p["address"] for path, p in calls if "geocode" in path] == [address, "吴山地铁站"]


@pytest.mark.parametrize("access", ["入口", "出口", "A口", "B口"])
def test_successful_original_access_is_kept_without_extra_requests(access):
    address = "吴山地铁站" + access
    client, calls = client_for(address, exact=True)
    result = client.locate(address, "福州市")
    assert result.status == "located"
    assert result.area_point is None
    assert result.candidates[0].label == address
    assert len(calls) == 1


@pytest.mark.parametrize("error", ["10001", "10003", "10016", "20000", "99999"])
def test_auth_quota_and_other_errors_are_not_retried(error):
    address = "吴山地铁站A口"
    client, calls = client_for(address, error=error)
    with pytest.raises(MapAPIError) as raised:
        client.locate(address, "福州市")
    assert raised.value.code == error
    assert len(calls) == 1


@pytest.mark.parametrize("address", ["合成小区入口", "示例医院出口", "吴山地铁旁合成小区入口", "地铁口便利店", "合成大厦A口"])
def test_non_station_entrances_and_names_are_not_trimmed(address):
    client, calls = client_for(address)
    with pytest.raises(MapAPIError):
        client.locate(address, "福州市")
    assert len(calls) == 1


def test_station_fallback_engine_failure_does_not_loop():
    address = "吴山地铁站A口"
    client, calls = client_for(address, fallback_error="30001")
    with pytest.raises(MapAPIError):
        client.locate(address, "福州市")
    assert len(calls) == 2


def test_station_fallback_does_not_confirm_a_station_in_another_city():
    address = "吴山地铁站A口"
    client, calls = client_for(address, fallback_city="杭州市")
    result = client.locate(address, "福州市")
    assert result.status != "located"
    assert result.area_point is None


@pytest.mark.parametrize("station,access", [("吴山站", "A口"), ("吴山站", "B口"), ("吴山站", "入口"), ("吴山站", "（A1出口）"), ("合成轻轨站", "B入口"), ("合成公交站", "出入口")])
def test_station_names_without_the_word_metro_also_support_access_fallback(station, access):
    address = station + access
    client, calls = client_for(address)
    result = client.locate(address, "福州市")
    assert result.area_point is not None
    assert result.status == "ambiguous"
    assert [params["address"] for path, params in calls if "geocode" in path][-1] == station


@pytest.mark.parametrize("name", ["吴山(地铁站)", "吴山地铁站", "吴山站", "吴山(地铁5号线)"])
@pytest.mark.parametrize("access", ["出口", "入口", "A出口", "B口", "A1口"])
def test_realistic_poi_station_aliases_produce_reference_instead_of_false_no_location(name, access):
    calls = []
    def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        payload = {"pois": [{"name": name, "cityname": "福州市", "adname": "仓山区", "location": "119.3,26.1", "id": "synthetic-station"}]} if "place/text" in request.url.path else {"geocodes": []}
        return httpx.Response(200, json={"status": "1", "infocode": "10000", **payload})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate("地铁5号线吴山地铁" + access, "福州市")
    assert result.area_point is not None
    assert result.status == "ambiguous"
    assert result.area_point.lng == 119.3
    assert "尚未确认具体入口或出口" in result.message
    assert len([1 for path, _ in calls if "geocode" in path]) == 2


@pytest.mark.parametrize("name,city,expected", [
    ("吴山(地铁站)A口", "福州市", True),
    ("吴山(地铁站)B口", "福州市", False),
    ("吴山(地铁站)", "福州市", False),
    ("吴山(地铁站)A口", "杭州市", False),
    ("吴山(地铁6号线)A口", "福州市", False),
    ("吴山地铁站便利店A口", "福州市", False),
    ("其他站(地铁站)A口", "福州市", False),
])
def test_station_aliases_do_not_confuse_exit_city_line_or_nearby_shop(name, city, expected):
    point = GeoPoint(lng=119.3, lat=26.1, city=city, name=name)
    assert _matches_landmark(point, "地铁5号线吴山地铁A出口", "福州市", "地铁5号线吴山地铁A出口") is expected


def test_mixed_station_and_other_exit_candidates_still_show_only_parent_reference():
    def handler(request):
        if "place/text" in request.url.path:
            payload = {"pois": [
                {"name": "其他站(地铁站)", "cityname": "福州市", "adname": "仓山区", "location": "120.3,27.1"},
                {"name": "吴山(地铁站)", "cityname": "福州市", "adname": "仓山区", "location": "119.3,26.1"},
                {"name": "吴山地铁站C西南口", "cityname": "福州市", "adname": "仓山区", "location": "119.3001,26.1001"},
                {"name": "吴山地铁站D东口", "cityname": "福州市", "adname": "仓山区", "location": "119.3002,26.1002"},
            ]}
        else:
            payload = {"geocodes": [{"formatted_address": "福州市仓山区吴山", "city": "福州市", "district": "仓山区", "location": "119.31,26.11", "level": "兴趣点"}]}
        return httpx.Response(200, json={"status": "1", "infocode": "10000", **payload})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate("地铁5号线吴山地铁A出口", "福州市")
    assert result.area_point is not None
    assert result.area_point.lng == 119.3
    assert result.area_point.lat == 26.1
    assert result.status == "ambiguous"
    assert "尚未确认具体入口或出口" in result.message


def test_station_alias_keeps_the_explicit_district_constraint():
    point = GeoPoint(lng=119.3, lat=26.1, city="福州市", district="鼓楼区", name="吴山(地铁站)A口")
    assert not _matches_landmark(point, "福州市仓山区地铁5号线吴山地铁A出口", "福州市", "地铁5号线吴山地铁A出口")
