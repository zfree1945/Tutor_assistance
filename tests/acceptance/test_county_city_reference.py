"""County-level cities use a verified prefecture/county/code scope for POIs."""
import httpx
import pytest

from tutor_screen.address_parts import split_address
from tutor_screen.maps import AmapClient, LOCATION_REVISION, MapAPIError


def poi(**changes):
    return {"name": "合成山小区", "cityname": "福州市", "adname": "福清市", "adcode": "350181",
            "id": "synthetic-estate", "address": "合成路48号", "location": "119.4,25.7", **changes}


def client_for(pois=None, *, region_changes=None, region_error=""):
    calls = []
    def handler(request):
        params = dict(request.url.params)
        calls.append((request.url.path, params))
        if "geocode" in request.url.path:
            if region_error:
                return httpx.Response(200, json={"status": "0", "infocode": region_error})
            payload = {"geocodes": [{"formatted_address": "福州市福清市", "city": "福州市", "district": "福清市",
                                     "adcode": "350181", "level": "区县", "location": "119.3,25.6", **(region_changes or {})}]}
        else:
            payload = {"pois": [poi()] if pois is None else pois}
        return httpx.Response(200, json={"status": "1", "infocode": "10000", **payload})
    return AmapClient("synthetic", transport=httpx.MockTransport(handler)), calls


@pytest.mark.parametrize("address,hint,parent,county", [
    ("福清市 合成山小区", "福州", "福州市", "福清市"),
    ("福州市福清市合成山小区", "福州", "福州市", "福清市"),
    ("福建省福清市合成山小区", "厦门市", "福州市", "福清市"),
    ("合成山小区", "福清市", "福州市", "福清市"),
    ("瑞安市示例小区", "温州", "温州市", "瑞安市"),
    ("乐清市示例小区", "", "温州市", "乐清市"),
    ("龙港市示例小区", "", "温州市", "龙港市"),
])
def test_explicit_county_hierarchy_is_parsed_without_losing_original(address, hint, parent, county):
    parts = split_address(address, hint)
    assert parts.city == parent and parts.district == county
    assert parts.original == address
    assert parts.landmark in {"合成山小区", "示例小区"}


def test_unique_estate_with_verified_county_and_code_produces_reference():
    client, calls = client_for([poi(), poi(name="合成山小区停车场", id="parking"), poi(name="便利店(合成山小区店)", id="shop")])
    result = client.locate("福清市 合成山小区", "福州")
    assert result.status == "ambiguous"
    assert result.area_point and result.area_point.precision == "area"
    assert result.area_point.label == "合成山小区"
    assert result.area_point.lng == 119.4 and result.area_point.lat == 25.7
    assert result.area_point.area_scale == "small"
    assert result.resolver_revision == LOCATION_REVISION
    assert len(result.candidates) == 1
    assert len(calls) == 2
    assert calls[0][1]["address"] == "福清市" and calls[0][1]["city"] == "福州市"
    assert calls[1][1]["keywords"] == "合成山小区"
    assert calls[1][1]["city"] == "350181" and calls[1][1]["citylimit"] == "true"


@pytest.mark.parametrize("changes", [
    {"cityname": "厦门市"}, {"adname": "鼓楼区"}, {"adcode": "350182"}, {"adcode": ""},
    {"name": "合成山小区停车场"}, {"name": "便利店(合成山小区店)"},
    {"cityname": ""}, {"adname": ""},
])
def test_mismatched_or_incomplete_poi_is_not_used_as_reference(changes):
    client, _ = client_for([poi(**changes)])
    result = client.locate("福清市 合成山小区", "福州")
    assert result.area_point is None and result.status != "located"


@pytest.mark.parametrize("changes", [{"adcode": ""}, {"adcode": "350100"}, {"city": "厦门市"}, {"district": "鼓楼区"}])
def test_county_scope_must_be_independently_verified(changes):
    client, _ = client_for(region_changes=changes)
    result = client.locate("福清市 合成山小区", "福州")
    assert result.area_point is None


def test_disjoint_same_named_estates_are_not_automatically_chosen():
    client, _ = client_for([poi(), poi(id="second", location="119.8,25.9")])
    result = client.locate("福清市 合成山小区", "福州")
    assert result.area_point is None
    assert len(result.candidates) == 2


def test_verified_county_scope_is_cached_within_the_client():
    client, calls = client_for()
    assert client.locate("福清市 合成山小区", "福州").area_point
    assert client.locate("福清市 合成山小区", "福州").area_point
    assert len([1 for path, _ in calls if "geocode" in path]) == 1


def test_provider_may_return_the_verified_county_in_the_city_field():
    client, _ = client_for([poi(cityname="福清市")], region_changes={"city": "福清市"})
    assert client.locate("福清市 合成山小区", "福州").area_point is not None


@pytest.mark.parametrize("error", ["10001", "10003", "30001"])
def test_region_failures_are_reported_without_guessing_an_estate(error):
    client, calls = client_for(region_error=error)
    with pytest.raises(MapAPIError) as raised:
        client.locate("福清市 合成山小区", "福州")
    assert raised.value.code == error
    assert len(calls) == 1
