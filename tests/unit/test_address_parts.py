"""结构化检索的行政区缺省、原文保留、范围组合及专名边界。"""
import httpx
import pytest
from unittest.mock import patch

from tutor_screen.address_parts import split_address
from tutor_screen.address_rules import address_query, validate_model_address
from tutor_screen.maps import AmapClient, GeoPoint, GeoResult, LOCATION_REVISION, _district_hint, _landmark
from tutor_screen.local_parser import parse_local
from tutor_screen.models import Order


@pytest.mark.parametrize("address,hint,city,district,landmark", [
    ("福州仓山九十九中附近", "", "福州市", "仓山区", "九十九中"),
    ("福州仓山区九十九中", "", "福州市", "仓山区", "九十九中"),
    ("福州九十九中", "", "福州市", "", "九十九中"),
    ("九十九中", "福州", "福州", "", "九十九中"),
    ("九十九中", "", "", "", "九十九中"),
    ("仓山九十九中", "福州", "福州", "仓山区", "九十九中"),
    ("仓山九十九中", "", "", "", "仓山九十九中"),
    ("福州晋安区保利合成住宅", "", "福州市", "晋安区", "保利合成住宅"),
    ("福州市台江区合成雅苑", "", "福州市", "台江区", "合成雅苑"),
    ("福建省福州鼓楼合成餐馆", "", "福州市", "鼓楼区", "合成餐馆"),
    ("南京市鼓楼区示例学校", "福州", "南京市", "鼓楼区", "示例学校"),
    ("长沙九十九中", "长沙", "长沙市", "", "九十九中"),
    ("福州仓山区示例镇合成示范院子B区", "", "福州市", "仓山区", "合成示范院子B区"),
])
def test_optional_administration_and_landmark(address, hint, city, district, landmark):
    parts = split_address(address, hint)
    assert parts.original == address
    assert (parts.city, parts.district, parts.landmark) == (city, district, landmark)
    assert parts.city_explicit is (address.startswith(("福州", "福建", "南京", "长沙")))


@pytest.mark.parametrize("relative", [
    "附近", "对面", "斜对面", "旁边", "隔壁", "边上", "周边", "周围", "一带",
    "东侧100m", "西侧约五百米", "南边1.5公里", "北面半公里", "东北方向约100米",
    "西北侧100M", "东南侧0.5KM", "西南方向两公里", "往东100米", "向北约200米",
    "以东100米", "北偏东100米", "100米左右", "100米以内", "100米以外",
    "约100-200米", "100～200m", "100米至200米", "东侧约100到200米范围",
])
def test_relative_forms_keep_raw_but_remove_range_from_search(relative):
    raw = "福州仓山九十九中" + relative
    parts = split_address(raw)
    assert parts.original == raw
    assert parts.relative == relative
    assert parts.query == "福州市仓山区九十九中"
    assert parts.landmark == "九十九中"
    assert address_query(raw) == "福州仓山九十九中"
    if any(unit in relative.lower() for unit in ("米", "m", "公里")):
        assert parts.distances


@pytest.mark.parametrize("address,relative", [
    ("靠近福州九十九中", "靠近"),
    ("距离福州九十九中100米", "距离 100米"),
    ("福州仓山近九十九中东侧100m", "近 东侧100m"),
])
def test_prefix_relations_preserve_the_anchor(address, relative):
    parts = split_address(address)
    assert parts.landmark == "九十九中"
    assert parts.relative == relative


@pytest.mark.parametrize("name", ["近江花园", "近水花园", "约克花园", "约翰公寓", "东南花园", "东北大学", "东方花园", "北大", "九十九中东门"])
def test_proper_names_are_not_relative_ranges(name):
    parts = split_address("福州晋安区" + name)
    assert parts.relative == ""
    assert parts.landmark == name
    assert parts.query == "福州市晋安区" + name


def test_direction_and_distance_are_separate_without_invented_coordinates():
    parts = split_address("福州仓山九十九中东侧100m")
    assert parts.directions == ("东侧",)
    assert parts.distances == ("100m",)
    assert parts.locality == "九十九中"
    assert _district_hint("福州晋安区保利合成住宅") == "晋安区"
    assert _landmark("福州晋安区保利合成住宅") == "保利合成住宅"


@pytest.mark.parametrize("relative", ["东侧100m", "西北侧约200米", "斜对面", "100-200米以内"])
def test_model_partial_address_restores_relative_suffix(relative):
    address = "福州九十九中" + relative
    assert validate_model_address("福州九十九中", "住在" + address) == address


@pytest.mark.parametrize("address,hint,city,keywords,area", [
    ("福州仓山九十九中东侧100m", "温州", "福州市", "九十九中", True),
    ("福州九十九中", "", "福州市", "九十九中", False),
    ("仓山九十九中附近", "福州", "福州", "九十九中", True),
    ("福州晋安区保利合成住宅", "", "福州市", "保利合成住宅", False),
])
def test_geocoder_uses_structured_fields_and_preserves_reference_semantics(address, hint, city, keywords, area):
    calls = []
    name = "保利合成住宅" if "保利" in address else "九十九中"
    district = "晋安区" if "保利" in address else "仓山区"
    def handler(request):
        calls.append(dict(request.url.params))
        payload = {"geocodes": []}
        if request.url.path.endswith("place/text"):
            payload = {"pois": [{"name": name, "cityname": "福州市", "adname": district,
                                 "location": "119.3,26.1", "address": "示例路"}]}
        return httpx.Response(200, json={"status": "1", **payload})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate(address, hint)
    assert calls[0]["city"] == city
    search = next(call for call in calls if "keywords" in call)
    assert search["keywords"] == keywords
    assert search["city"] == city
    assert search["citylimit"] == "true"
    assert result.status == "located"
    assert bool(result.area_point) is area
    if area:
        assert result.area_point.precision == "area"
        assert result.area_point.lng == 119.3 and result.area_point.lat == 26.1


def test_wrong_district_poi_is_not_automatically_accepted_after_normalization():
    def handler(request):
        return httpx.Response(200, json={"status": "1", "geocodes": [], "pois": [{
            "name": "九十九中", "cityname": "福州市", "adname": "鼓楼区", "location": "119.3,26.1"}]})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate("福州仓山九十九中附近")
    assert result.status != "located"


@pytest.mark.parametrize("address", ["福州仓山近九十九中", "福州九十九中西北侧100米", "福州九十九中往东100m"])
def test_local_order_and_even_exact_geocode_keep_relative_uncertainty(address):
    order = parse_local("地址：" + address + "\n初一数学，100元/小时")
    assert order.address == address
    assert "地址范围" in order.uncertain_fields
    def handler(request):
        return httpx.Response(200, json={"status": "1", "geocodes": [{
            "formatted_address": "福州市仓山区九十九中", "city": "福州市", "location": "119.3,26.1", "level": "门牌号"}]})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate(address)
    assert result.area_point and result.area_point.precision == "area"


@pytest.mark.parametrize("manual", [False, True])
def test_previous_exact_cache_of_new_relative_form_refreshes_except_manual_point(manual):
    from tutor_screen.map_ui import prepare_batch
    address = "福州仓山近九十九中"
    cached = GeoResult(status="located", resolver_revision=LOCATION_REVISION - 1,
                       candidates=[GeoPoint(lng=119.3, lat=26.1, source="user" if manual else "amap")])
    with patch("tutor_screen.map_ui.lookup_geo", return_value=cached), patch("tutor_screen.map_ui.current_city", return_value="福州"):
        batch = prepare_batch([Order(order_id="test", raw_text="地址：" + address, address=address)])
    assert bool(batch["pending"]) is (not manual)
