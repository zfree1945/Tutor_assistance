"""Numbered school searches yield verified nearby references, not exact venues."""
import httpx
import pytest

from tutor_screen.maps import AmapClient, LOCATION_REVISION, MapAPIError, _numbered_school, GeoPoint, GeoResult


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr(AmapClient, "_pace_request", lambda self: None)


def school(name="福州市第十六中学", city="福州市", district="仓山区", location="119.3,26.1"):
    return {"name": name, "cityname": city, "adname": district, "location": location, "address": "合成路35号"}


def client_for(pois, *, error=""):
    calls = []
    def handler(request):
        params = dict(request.url.params)
        calls.append((request.url.path, params))
        if "geocode" in request.url.path:
            if error:
                return httpx.Response(200, json={"status": "0", "infocode": error})
            payload = {"geocodes": []}
        else:
            payload = {"pois": pois if params["keywords"] == "第十六中学" else [school("烟台山公园")]}
        return httpx.Response(200, json={"status": "1", "infocode": "10000", **payload})
    return AmapClient("synthetic", transport=httpx.MockTransport(handler)), calls


@pytest.mark.parametrize("address", ["烟台山16 中附近", "烟台山16中附近", "烟台山十六中附近", "烟台山第16中学附近"])
def test_verified_numbered_school_can_anchor_a_nearby_area(address):
    client, calls = client_for([school()])
    result = client.locate(address, "福州")
    assert result.status == "ambiguous"
    assert result.area_point is not None
    assert result.area_point.precision == "area" and result.area_point.area_scale == "small"
    assert result.area_point.label == "福州市第十六中学"
    assert "附近参考范围" in result.message and "具体上课地点待确认" in result.message
    assert result.resolver_revision == LOCATION_REVISION
    supplementary = [params for path, params in calls if params.get("keywords") == "第十六中学"]
    assert len(supplementary) == 1
    assert supplementary[0]["city"] == "福州"
    assert supplementary[0]["citylimit"] == "true"
    assert all("16" in p["address"] or "十六" in p["address"] for path, p in calls if "geocode" in path)


@pytest.mark.parametrize("pois", [
    [], [school("福州市第六中学")], [school("福州市第十六小学")],
    [school("福州市第十六中学便利店")], [school(city="厦门市")],
    [school("福州市第十六中学(东校区)"), school("福州市第十六中学(西校区)", location="119.5,26.2")],
])
def test_unverified_school_candidates_do_not_create_a_reference(pois):
    client, calls = client_for(pois)
    result = client.locate("烟台山16 中附近", "福州")
    assert result.area_point is None
    assert result.status != "located"


def test_school_fallback_after_geocode_engine_error_is_bounded():
    client, calls = client_for([school()], error="30001")
    result = client.locate("烟台山16 中附近", "福州")
    assert result.area_point is not None
    assert len(calls) == 2


@pytest.mark.parametrize("error", ["10001", "10003", "10016"])
def test_auth_quota_and_busy_errors_are_not_hidden_by_school_search(error):
    client, calls = client_for([school()], error=error)
    with pytest.raises(MapAPIError) as raised:
        client.locate("烟台山16 中附近", "福州")
    assert raised.value.code == error
    assert len(calls) == 1


def test_numbered_school_fallback_requires_a_city_context():
    client, calls = client_for([school()])
    result = client.locate("烟台山16 中附近")
    assert result.area_point is None
    assert not any(p.get("keywords") == "第十六中学" for _, p in calls)


@pytest.mark.parametrize("number,chinese", [(1, "一"), (10, "十"), (16, "十六"), (99, "九十九"), (101, "一百零一"), (110, "一百一十")])
def test_arabic_and_chinese_numbered_school_names_have_same_identity(number, chinese):
    assert _numbered_school(f"第{number}中学") == _numbered_school(f"第{chinese}中学") == (chinese, "中学")


def test_school_reference_rejects_a_different_explicit_district():
    client, calls = client_for([school(district="鼓楼区")])
    result = client.locate("仓山区烟台山16 中附近", "福州")
    assert result.area_point is None


def test_an_existing_school_reference_does_not_trigger_another_search(monkeypatch):
    client, calls = client_for([])
    reference = GeoResult(status="located", area_point=GeoPoint(lng=119.3, lat=26.1, precision="area"), resolver_revision=LOCATION_REVISION)
    monkeypatch.setattr(client, "_locate_address", lambda *args: reference)
    assert client.locate("烟台山16 中附近", "福州") is reference
    assert not calls
