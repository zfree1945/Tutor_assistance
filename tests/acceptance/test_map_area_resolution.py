"""区域定位语义和逐级回退；全部使用合成地名与隔离HTTP。"""
import httpx
import pytest

from tutor_screen.maps import AmapClient, GeoPoint, GeoResult, LOCATION_REVISION, MapAPIError, _landmark, area_queries


def code(name, level="道路", location="120.70,28.00", city="温州市"):
    return {"formatted_address": name, "level": level, "location": location, "city": city}


def client_for(geocodes, pois=None, fallback=None):
    calls = []
    def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("place/text"):
            payload = {"pois": pois or []}
        else:
            query = request.url.params["address"]
            payload = {"geocodes": fallback.get(query, geocodes) if fallback else geocodes}
        return httpx.Response(200, json={"status": "1", "infocode": "10000", **payload})
    return AmapClient("synthetic", transport=httpx.MockTransport(handler)), calls


@pytest.mark.parametrize("address,level", [
    ("温州市鹿城区人民路", "道路"),
    ("温州市鹿城区人民路与学院路交叉口", "道路"),
    ("温州市鹿城区示例街道", "街道"),
    ("温州市鹿城区示例镇", "乡镇"),
    ("温州市鹿城区示例乡", "乡镇"),
    ("温州市鹿城区示例村", "村庄"),
    ("温州市鹿城区示例社区", "社区"),
    ("温州市鹿城区示例商圈", "热点商圈"),
    ("温州市鹿城区示例开发区", "开发区"),
    ("温州市鹿城区", "区县"),
    ("温州市", "市"),
])
def test_known_coarse_regions_get_a_visible_reference_without_becoming_exact(address, level):
    client, calls = client_for([code(address, level)])
    result = client.locate(address, "温州市")
    assert result.area_point is not None
    assert result.area_point.precision == "area" and result.area_point.label == address
    assert result.area_point.lng == 120.7
    assert result.area_point.area_scale == ("medium" if level in {"道路", "社区", "村庄", "热点商圈"} else "large")
    assert result.status == "ambiguous" and result.resolver_revision == LOCATION_REVISION
    assert len(calls) <= 2


@pytest.mark.parametrize("landmark", ["示例中学", "示例小学", "示例医院", "示例商场", "示例超市", "示例体育馆", "示例地铁站", "示例公交站", "示例产业园", "示例大学校区"])
@pytest.mark.parametrize("relative", ["附近", "对面", "旁边"])
def test_verified_landmark_nearby_becomes_area_reference(landmark, relative):
    pois = [{"name": landmark, "cityname": "温州市", "adname": "鹿城区", "address": "人民路", "location": "120.7,28"}]
    client, calls = client_for([], pois)
    result = client.locate("鹿城区人民路" + landmark + relative, "温州市")
    assert result.area_point is not None and result.area_point.label == landmark
    assert result.area_point.precision == "area"
    assert result.area_point.area_scale == "small"
    assert len(calls) == 2


@pytest.mark.parametrize("address,name,district,landmark", [
    ("瓯海区南白象近万象城", "温州万象城", "瓯海区", "万象城"),
    ("苍南灵溪三中附近", "灵溪三中", "苍南县", "苍南灵溪三中"),
])
def test_short_nearby_and_school_names_match_unique_poi(address, name, district, landmark):
    assert _landmark(address) == landmark
    pois = [{"name": name, "cityname": "温州市", "adname": district,
             "address": "", "location": "120.7,28"}]
    client, calls = client_for([], pois)
    result = client.locate(address, "温州市")
    assert result.status == "located"
    assert len(result.candidates) == 1 and result.candidates[0].label.startswith(name + " · ")
    assert result.area_point and result.area_point.label == landmark
    assert result.area_point.area_scale == "small"
    assert result.area_point.precision == "area"
    assert len(calls) == 2


@pytest.mark.parametrize("school", ["一中", "二中", "三中", "十小", "12中", "3小"])
def test_school_abbreviations_are_landmarks(school):
    assert _landmark("苍南县灵溪" + school + "附近") == "灵溪" + school


@pytest.mark.parametrize("address,landmark,queries", [
    ("瓯海区南白象近万象城", "万象城", ["瓯海区"]),
    ("瓯海区南白象近万象城附近", "万象城", ["瓯海区"]),
    ("瓯海区近万象城", "万象城", ["瓯海区"]),
    ("南白象街道近万象城", "万象城", ["南白象街道"]),
    ("瓯海区南白象近示例路万象城", "万象城", ["瓯海区"]),
    ("近江花园", "近江花园", []),
    ("上城区近江花园", "近江花园", ["上城区"]),
    ("上城区近江路示例学校", "示例学校", ["上城区近江路", "上城区"]),
    ("幸福近水花园", "幸福近水花园", []),
])
def test_near_separator_requires_context_and_preserves_proper_names(address, landmark, queries):
    assert _landmark(address) == landmark
    assert area_queries(address) == queries


@pytest.mark.parametrize("address,nearby", [
    ("瓯海区南白象近万象城", True),
    ("上城区近江花园", False),
    ("上城区近江路28号", False),
])
def test_short_nearby_semantics_also_apply_to_exact_geocodes(address, nearby):
    client, calls = client_for([code(address, "门牌号")])
    result = client.locate(address, "温州市")
    assert result.status == "located"
    assert bool(result.area_point) is nearby
    assert result.candidates[0].precision == "exact"
    if nearby:
        assert result.area_point.precision == "area"
    assert len(calls) == 1


def test_nearby_geocoded_door_number_does_not_become_exact_student_location():
    client, _ = client_for([code("温州市鹿城区人民路28号", "门牌号")])
    result = client.locate("鹿城区人民路28号对面", "温州市")
    assert result.area_point and result.area_point.precision == "area"
    assert result.area_point.area_scale == "small"


def test_multiple_exits_keep_candidates_and_show_representative_position():
    codes = [code("温州市鹿城区示例学校东门", "兴趣点", "120.7,28"),
             code("温州市鹿城区示例学校西门", "兴趣点", "120.704,28.004")]
    client, _ = client_for(codes)
    result = client.locate("示例学校", "温州市")
    assert result.area_point and result.area_point.lng == pytest.approx(120.702)
    assert result.area_point.lat == pytest.approx(28.002)
    assert result.area_point.label == "示例学校"
    assert result.area_point.area_scale == "small"
    assert len(result.candidates) == 2


def test_dispersed_candidates_are_not_averaged_or_replaced_by_first_point():
    client, _ = client_for([code("温州市同名小区东区", "住宅区", "120.7,28"),
                            code("温州市同名小区西区", "住宅区", "121.1,28.3")])
    result = client.locate("同名小区", "温州市")
    assert result.status == "ambiguous" and result.area_point is None
    assert len(result.candidates) == 2


def test_missing_building_falls_back_to_explicit_road_before_district():
    client, calls = client_for([], fallback={"鹿城区人民路": [code("温州市鹿城区人民路")]})
    result = client.locate("鹿城区人民路示例公寓3栋", "温州市")
    assert result.area_point and result.area_point.label == "温州市鹿城区人民路"
    assert result.area_point.area_scale == "medium"
    assert [params.get("address") for path, params in calls if path.endswith("geocode/geo")] == ["鹿城区人民路示例公寓3栋", "鹿城区人民路"]
    assert all(set(params) <= {"address", "city", "key", "keywords", "offset", "page", "extensions", "citylimit"} for _, params in calls)


def test_distributed_candidates_can_fall_back_to_explicit_district_center():
    codes = [code("温州市鹿城区同名小区东区", "住宅区", "120.7,28"),
             code("温州市鹿城区同名小区西区", "住宅区", "121.1,28.3")]
    client, _ = client_for(codes, fallback={"鹿城区": [code("温州市鹿城区", "区县")]})
    result = client.locate("鹿城区同名小区", "温州市")
    assert result.area_point and result.area_point.label == "温州市鹿城区"
    assert result.area_point.area_scale == "large"
    assert len(result.candidates) == 2


@pytest.mark.parametrize("name,city", [("杭州市鹿城区人民路", "杭州市"), ("温州市瓯海区人民路", "温州市")])
def test_other_city_or_district_is_not_used_as_original_region(name, city):
    client, _ = client_for([code(name, city=city)])
    result = client.locate("鹿城区人民路", "温州市")
    assert result.area_point is None


@pytest.mark.parametrize("level,name", [("国家", "中国"), ("省", "浙江省"), ("未知", "温州市鹿城区")])
def test_country_province_and_unknown_level_do_not_become_region_marker(level, name):
    client, _ = client_for([code(name, level)])
    assert client.locate("鹿城区人民路", "温州市").area_point is None


def test_empty_or_vague_address_does_not_get_default_city_center():
    client, calls = client_for([])
    result = client.locate("待定", "温州市")
    assert result.area_point is None and result.status == "not_found"
    assert len(calls) == 2


def test_fallback_requests_are_bounded_and_contain_only_clean_geographic_input():
    client, calls = client_for([])
    client.locate("温州市鹿城区示例街道人民路示例公寓，电话13800138000，课酬200元/小时", "温州市")
    assert len(calls) == 5
    assert "13800138000" not in str(calls) and "200元" not in str(calls)
    assert area_queries("温州市鹿城区示例街道人民路示例公寓")[:3] == ["温州市鹿城区示例街道人民路", "温州市鹿城区示例街道", "温州市鹿城区"]


def test_quota_failure_in_fallback_stops_immediately_without_trying_broader_areas():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 3:
            return httpx.Response(200, json={"status": "0", "infocode": "10003"})
        return httpx.Response(200, json={"status": "1", "geocodes": [], "pois": []})
    client = AmapClient("synthetic", transport=httpx.MockTransport(handler))
    with pytest.raises(MapAPIError) as error:
        client.locate("鹿城区人民路示例公寓", "温州市")
    assert error.value.quota_exhausted and len(calls) == 3


def test_area_result_roundtrips_without_changing_legacy_cache_shape():
    point = GeoPoint(lng=120.7, lat=28, city="温州市", label="鹿城区人民路", precision="area")
    result = GeoResult(status="ambiguous", area_point=point, resolver_revision=LOCATION_REVISION)
    assert GeoResult.model_validate_json(result.model_dump_json()).area_point == point
    assert GeoResult.model_validate({"status": "ambiguous", "candidates": []}).area_point is None


def test_missing_city_field_does_not_let_other_city_label_inherit_default_city():
    client, _ = client_for([code("杭州市鹿城区人民路", city="")])
    assert client.locate("鹿城区人民路", "温州市").area_point is None


def test_city_query_cannot_match_an_empty_remainder_in_unrelated_label():
    client, _ = client_for([code("全国", "市")])
    assert client.locate("温州市", "温州市").area_point is None


def test_multiple_district_results_do_not_get_mislabeled_as_specific_road():
    client, _ = client_for([code("温州市鹿城区", "区县", "120.7,28"), code("温州市鹿城区", "区县", "120.702,28.002")])
    area = client.locate("温州市鹿城区人民路", "温州市").area_point
    assert area and area.label == "温州市鹿城区"


@pytest.mark.parametrize("query,level,scale", [
    ("鹿城区示例街道", "街道", "large"),
    ("鹿城区示例街道", "道路", "large"),
    ("鹿城区示例社区", "社区", "medium"),
    ("鹿城区示例村", "村庄", "medium"),
])
def test_area_fallback_scale_respects_broad_query(query, level, scale):
    client, calls = client_for([], fallback={query: [code("温州市" + query, level)]})
    result = client.locate(query + "示例公寓3栋", "温州市")
    assert result.area_point and result.area_point.area_scale == scale
    assert len(calls) <= 5


@pytest.mark.parametrize("query,scale", [("鹿城区", "large"), ("鹿城区人民路", "medium")])
def test_region_clusters_without_landmarks_keep_region_scale(query, scale):
    client, _ = client_for([code("温州市" + query, "道路", "120.7,28"),
                            code("温州市" + query, "道路", "120.702,28.002")])
    result = client.locate(query, "温州市")
    assert result.area_point and result.area_point.area_scale == scale
