"""九条虚构地点及编号的福州格式订单：接口响应、坐标及 POI 标识均为离线合成数据。"""
import httpx
import pytest

from tutor_screen.maps import AmapClient, _landmark
from tutor_screen.models import Order


# 保留测试格式，地点与编号均为虚构值，不纠正年级、报价和日程等本次范围外的字段。
CASES = [
    ("fz593203", "福州晋安区合成居，四年级数学英语，120-180两小时，周一到周四晚上，男生要女老师、数学系或英语系",
     "福州晋安区合成居", "合成居", "合成居", "晋安区", "350111"),
    ("fz593292", "福州仓山九十九中附近，高三物理基础差，220-240两小时，周日早上10-12，女生要女老师",
     "福州仓山九十九中附近", "九十九中", "福州市第九十九中学", "仓山区", "350104"),
    ("fz593291", "福州合成示范家园，五年级数学，120-140小时，周24晚上，男生要男老师",
     "福州合成示范家园", "合成示范家园", "合成示范家园", "晋安区", "350111"),
    ("fz593290", "福州合成环示例广场合成香苑，一年级英语，100-150两小时，周1245晚上，女生要女老师、英专或者有英语教资",
     "福州合成环示例广场合成香苑", "合成环示例广场合成香苑", "合成香苑", "晋安区", "350111"),
    ("fz593299", "福州仓山区示例镇合成示范院子B区，初一英语56分（顺带数学语文辅导作业），150两小时，周一到周五晚上7-9，男生要女老师",
     "福州仓山区示例镇合成示范院子B区", "合成示范院子B区", "合成示范院子(B区)", "仓山区", "350104"),
    ("fz593296", "福州晋安区合成新城，初一语数英地理辅导，150两小时，周六和周日各一次，女生要女老师",
     "福州晋安区合成新城", "合成新城", "合成新城", "晋安区", "350111"),
    ("fz593294", "福州晋安区保利合成住宅，初二全科辅导 基础不好，210-240/3两小时，周1245晚上7-10，男生要求男女不限",
     "福州晋安区保利合成住宅", "保利合成住宅", "保利合成住宅", "晋安区", "350111"),
    ("fz593293", "福州市台江区合成雅苑，五年级语数英（英语在学校是学初一内容），120-140两小时，周六早上，男生要求男女不限",
     "福州市台江区合成雅苑", "合成雅苑", "合成雅苑", "台江区", "350103"),
    ("fz593282", "s福州市鼓楼区合成餐馆，二年级硬笔书法，120-150两小时，周末，女生要女老师",
     "福州市鼓楼区合成餐馆", "合成餐馆", "合成餐馆", "鼓楼区", "350102"),
]


@pytest.mark.parametrize("number,body,address,keyword,name,district,adcode", CASES, ids=[row[0] for row in CASES])
def test_nine_orders_search_valid_place_and_verify_structured_response(number, body, address, keyword, name, district, adcode):
    order = Order(order_id=number, order_number=number, raw_text=number + "\n" + body, address=address)
    before = order.model_dump()
    calls = []
    def handler(request):
        params = dict(request.url.params)
        calls.append((request.url.path, params))
        payload = {"geocodes": []}
        if request.url.path.endswith("place/text"):
            payload = {"pois": [{"name": name, "cityname": "福州市", "adname": district,
                                 "adcode": adcode, "id": "synthetic-" + number,
                                 "address": "合成路", "location": "119.3,26.1"}]}
        elif params["address"] == district:
            payload = {"geocodes": [{"formatted_address": "福建省福州市" + district, "city": "福州市",
                                     "district": district, "adcode": adcode, "level": "区县", "location": "119.3,26.1"}]}
        return httpx.Response(200, json={"status": "1", **payload})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate(order.address, "温州")
    assert result.status == "located"
    assert order.model_dump() == before
    assert _landmark(address) == keyword
    search = next(params for path, params in calls if path.endswith("place/text"))
    explicit_district = any(token in address for token in ("仓山", "晋安", "台江", "鼓楼"))
    assert search["city"] == (adcode if explicit_district else "福州市")
    assert search["citylimit"] == "true" and search["keywords"] == keyword
    assert search["extensions"] == "all"
    point = result.candidates[0]
    assert (point.city, point.district, point.adcode, point.poi_id, point.name) == (
        "福州市", district, adcode, "synthetic-" + number, name)
    assert bool(result.area_point) is (number == "fz593292")
    assert all("课酬" not in str(params) and "两小时" not in str(params) for _, params in calls)


@pytest.mark.parametrize("landmark", ["保利合成住宅", "合成雅苑", "合成居", "合成餐馆", "悦里", "一中"])
def test_valid_landmarks_do_not_require_city_or_special_name_characters(landmark):
    assert _landmark(landmark) == landmark


@pytest.mark.parametrize("value", ["待定", "未知", "福州市", "仓山区", "鼓楼区人民路", "初一数学", "每周两次"])
def test_regions_and_business_text_are_not_poi_names(value):
    assert _landmark(value) == ""
