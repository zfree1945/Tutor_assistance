"""本地与模型协作提取的原文约束，以及高德请求与显示语义。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen.address_rules import address_query
from tutor_screen.llm_client import extract_order
from tutor_screen.local_parser import parse_local
from tutor_screen.maps import AmapClient, GeoPoint, GeoResult, LOCATION_REVISION, _landmark
from tutor_screen.matching import evaluate_order
from tutor_screen.models import Criteria, Order
from tutor_screen.pipeline import process_batch


def response(address):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps({"address": address, "field_sources": {"address": "模型伪造来源"}}, ensure_ascii=False),
    ))])


def extract(raw, address):
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = response(address)
        return extract_order(raw, "synthetic", "https://example.test", "synthetic")


@pytest.mark.parametrize("address", [
    "温州龙湾滨海合成家园", "龙湾或瓯北", "瓯海区合成桥近示例城", "近示例城",
    "约在示例城附近", "示例城东侧约500米", "示例学校对面", "示例小区 附近",
    "示例学校西南方向约1.5公里", "上城区近江花园", "幸福近水花园",
])
def test_model_address_is_verbatim_and_has_a_real_source(address):
    raw = f"初一数学\n地址：{address}\n时间：周六"
    parsed = extract(raw, address)
    assert parsed.address == parsed.field_sources["address"] == address
    assert parsed.address in raw


@pytest.mark.parametrize("address,shortened", [
    ("瓯海区合成桥近示例城", "示例城"), ("龙湾或瓯北", "龙湾"),
    ("示例城东侧约500米", "示例城"), ("示例小区 附近", "示例小区"),
])
def test_explicit_local_span_restores_model_omissions(address, shortened):
    assert extract(f"初一数学\n地址：{address}\n时间：周六", shortened).address == address


@pytest.mark.parametrize("context,address", [
    ("住在近示例城", "近示例城"), ("住在约在示例城附近", "约在示例城附近"),
    ("住在示例城东侧约500米", "示例城东侧约500米"),
    ("位于瓯海区合成桥近示例城", "瓯海区合成桥近示例城"),
    ("住在温州市瓯海区示例城东侧约五百米", "温州市瓯海区示例城东侧约五百米"),
])
def test_body_address_survives_local_normalization_refilter_and_json_restore(context, address):
    raw = f"订单号 MODEL101 初一数学 100/h 周六\n{context}"
    assert parse_local(raw).address is None
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = response("示例城")
        order, = process_batch(raw, "synthetic", "https://example.test", "synthetic")
        assert client.return_value.chat.completions.create.call_count == 1
    assert order.address == address and order.mode == "线下"
    assert "地址范围" in order.uncertain_fields
    assert "地址待确认" not in order.uncertain_fields
    restored = Order.model_validate_json(order.model_dump_json())
    with patch("tutor_screen.pipeline.extract_order") as model:
        for _ in range(2):
            restored = evaluate_order(restored, Criteria())
            assert restored.address == restored.field_sources["address"] == address
        model.assert_not_called()


@pytest.mark.parametrize("address", [
    "温州市示例城", "示例城500号", "示例城附近（瓯海区）", "示例城\n对象:初一学生",
    "示例城 对象:初一学生", "示例城 电话:13800138000", "王测试", "private_wx", "█" * 3,
])
def test_fabrication_pollution_and_masked_contacts_are_not_accepted(address):
    raw = "初一数学\n住在示例城附近\n姓名：王测试\n微信：private_wx\n电话：13800138000"
    parsed = extract(raw, address)
    assert parsed.address is None and "address" not in parsed.field_sources
    assert "模型地址未通过原文校验" in parsed.uncertain_fields


@pytest.mark.parametrize("address", ["数学", "周六", "有耐心"])
def test_exact_substring_in_an_unrelated_field_is_not_an_address(address):
    raw = "科目：数学\n时间：周六\n要求：有耐心"
    assert extract(raw, address).address is None


def test_invalid_model_address_uses_local_evidence_and_does_not_stop_later_orders():
    raw = "订单号 MODEL101 初一数学\n地址：示例小区附近\n订单号 MODEL102 初一英语\n住在示例城对面"
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.side_effect = [response("虚构小区"), response("示例城对面")]
        orders = process_batch(raw, "synthetic", "https://example.test", "synthetic")
    assert [o.address for o in orders] == ["示例小区附近", "示例城对面"]
    assert "原文校验" in orders[0].extraction_note
    assert all(o.address in o.raw_text for o in orders)


def test_failure_keeps_local_address_and_missing_body_address_pending():
    raw = "订单号 MODEL101 初一数学\n地址：示例小区附近\n订单号 MODEL102 初一英语\n住在示例城对面"
    with patch("tutor_screen.pipeline.extract_order", side_effect=RuntimeError("synthetic")):
        first, second = process_batch(raw, "synthetic", "https://example.test", "synthetic")
    assert first.address == "示例小区附近" and first.extraction_source == "local"
    assert second.address is None and "本地规则" in second.extraction_note
    assert "地址待确认" in second.uncertain_fields


def test_conflicting_addresses_keep_explicit_local_address_and_request_review():
    raw = "订单号 MODEL101 初一数学\n地址：示例小区\n备注：家住示例城附近"
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = response("示例城附近")
        order, = process_batch(raw, "synthetic", "https://example.test", "synthetic")
    assert order.address == "示例小区" and "地址存在多个候选" in order.uncertain_fields
    assert "不一致" in order.extraction_note


def test_model_cannot_restore_a_masked_name_by_expanding_local_span():
    parsed = extract("初一数学\n地址：示例城 姓名：王测试", "示例城")
    assert parsed.address == "示例城"
    assert "王测试" not in parsed.field_sources["address"]


def test_complete_model_address_does_not_absorb_the_preceding_narrative():
    assert extract("初一数学\n就选示例城附近", "示例城附近").address == "示例城附近"


def test_body_address_restores_original_city_prefix_without_inventing_a_city():
    assert extract("初一数学\n位于温州市瓯海区示例城", "示例城").address == "温州市瓯海区示例城"
    assert extract("初一数学\n位于示例城", "示例城").address == "示例城"


@pytest.mark.parametrize("address,query,landmark", [
    ("近示例城", "示例城", "示例城"),
    ("靠近示例城", "示例城", "示例城"),
    ("约在示例城附近", "示例城", "示例城"),
    ("瓯海区合成桥近示例城", "瓯海区合成桥示例城", "示例城"),
    ("瓯海区示例城东侧约500米", "瓯海区示例城", "示例城"),
    ("示例城约500米", "示例城", "示例城"),
    ("示例城西南方向约1.5公里", "示例城", "示例城"),
    ("上城区近江花园", "上城区近江花园", "近江花园"),
    ("幸福近水花园", "幸福近水花园", "幸福近水花园"),
    ("约克花园", "约克花园", "约克花园"),
    ("约翰公寓", "约翰公寓", "约翰公寓"),
    ("瓯海区示例城东侧约五百米", "瓯海区示例城", "示例城"),
    ("示例城约两公里", "示例城", "示例城"),
    ("示例城约半公里", "示例城", "示例城"),
    ("示例城约500m", "示例城", "示例城"),
    ("示例城约1.5KM", "示例城", "示例城"),
])
def test_map_query_removes_qualifiers_without_changing_original(address, query, landmark):
    assert address_query(address) == query
    assert _landmark(address) == landmark


@pytest.mark.parametrize("address", ["近示例城", "约在示例城附近", "示例城东侧约500米"])
def test_even_exact_geocode_of_relative_address_is_only_an_area_reference(address):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "1", "geocodes": [{
            "formatted_address": "温州市瓯海区示例城", "level": "门牌号", "city": "温州市", "location": "120.7,28.0",
        }]})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate(address, "温州市")
    assert requests[0].url.params["address"] == "示例城"
    assert result.area_point is not None and result.area_point.precision == "area"
    assert result.resolver_revision == LOCATION_REVISION


def test_relative_poi_search_uses_landmark_and_keeps_reference_semantics():
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("place/text"):
            return httpx.Response(200, json={"status": "1", "pois": [{
                "name": "示例城", "cityname": "温州市", "adname": "瓯海区", "address": "", "location": "120.7,28.0",
            }]})
        return httpx.Response(200, json={"status": "1", "geocodes": []})
    result = AmapClient("synthetic", transport=httpx.MockTransport(handler)).locate("瓯海区示例城东侧约500米", "温州市")
    assert requests[-1].url.params["keywords"] == "示例城"
    assert requests[1].url.params["address"] == "瓯海区"
    assert result.area_point.precision == "area"


@pytest.mark.parametrize("manual", [False, True])
def test_old_relative_cache_is_refreshed_but_manual_points_are_kept(manual):
    from tutor_screen.map_ui import prepare_batch
    order = Order(order_id="MODEL101", raw_text="地址：示例城约500米", address="示例城约500米")
    cache = GeoResult(status="located", resolver_revision=3, candidates=[GeoPoint(
        lng=120.7, lat=28, precision="exact", source="user" if manual else "amap",
    )])
    with patch("tutor_screen.map_ui.lookup_geo", return_value=cache), patch("tutor_screen.map_ui.current_city", return_value="温州市"):
        batch = prepare_batch([order])
    assert bool(batch["pending"]) is not manual


def test_import_ui_explains_address_sharing_and_restores_model_address():
    from tutor_screen import batch_store
    raw = "订单号 MODEL101 初一数学 100/h 周六\n住在示例城东侧约500米"
    app = AppTest.from_file(Path(__file__).resolve().parents[2] / "app.py", default_timeout=20)
    app.session_state["api_key"] = "synthetic"
    app.run()
    assert any("DeepSeek" in item.value and "地址" in item.value for item in app.caption)
    app.text_area(key="order_text").set_value(raw).run()
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = response("示例城")
        app.button(key="apply_filters").click().run()
    assert not app.exception
    assert app.session_state["filtered"][0].address == "示例城东侧约500米"
    assert batch_store.load_batch().orders[0].address == "示例城东侧约500米"
    with patch("tutor_screen.pipeline.extract_order") as model:
        restored = AppTest.from_file(Path(__file__).resolve().parents[2] / "app.py", default_timeout=20).run()
        assert not restored.exception
        assert restored.session_state["filtered"][0].address == "示例城东侧约500米"
        model.assert_not_called()
