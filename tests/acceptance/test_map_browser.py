"""Chromium acceptance for map DOM and component events, fully offline."""
from pathlib import Path
import json
from uuid import uuid4

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright, TimeoutError as BrowserTimeout

from map_browser_support import ROOT, deny_external_requests, start_isolated_server

ARTIFACTS = ROOT / "artifacts" / "acceptance" / f"maps_v102_qa_{uuid4().hex[:8]}"
SDK = Path(__file__).with_name("amap_sdk_mock.js").read_text(encoding="utf-8")

COMPONENT_WRAPPER = '''from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import streamlit as st
from tutor_screen.map_component import render_map
st.set_page_config(layout="wide")
st.title("地图组件离线验收")
picking = st.checkbox("地图选点模式", key="test_picking")
points = [
    {"id":"r1","lng":120.70,"lat":28.00,"number":"MAP-101","status":"明确匹配","subject":"数学","grade":"小学","pay":"150 元/小时","distance_label":"约 1.2 公里"},
    {"id":"r2","lng":120.71,"lat":28.01,"number":"MAP-102","status":"可能匹配","subject":"<img src=x onerror=window.__injected=true>","grade":"初中","pay":"180 元/小时","distance_label":"约 2.3 公里"},
]
result = render_map(points, origin={"lng":120.69,"lat":27.99,"label":"测试出发点"},
                    selected_id=st.session_state.get("test_selected"), picking=picking,
                    js_key="SYNTHETIC-JS-KEY", security_code="SYNTHETIC-SECURITY-CODE")
if result.selected:
    st.session_state.test_selected = result.selected["id"]
    st.rerun()
if result.picked:
    st.session_state.test_picked = result.picked
st.caption("已选择 " + st.session_state.get("test_selected", "无"))
if st.session_state.get("test_picked"):
    st.caption("已选坐标 " + str(st.session_state.test_picked["lng"]) + "," + str(st.session_state.test_picked["lat"]))
'''

APP_WRAPPER = '''from pathlib import Path
import json
import runpy
import sys
import time
sys.path.insert(0, str(Path(__file__).parent))
import httpx
import streamlit as st
from tutor_screen import map_store, llm_client
from tutor_screen.local_parser import LOCAL_FIELDS_REVISION
from tutor_screen.maps import AmapClient, GeoPoint, MapCredentials, MapAPIError
from tutor_screen.models import Order
root=Path(__file__).parent
def forbidden(*args, **kwargs):
    raise AssertionError("No real model or map requests in isolated browser acceptance")
llm_client._client=forbidden
httpx.HTTPTransport.handle_request=forbidden
def fake_request(self, endpoint, params):
    with (root/"map_calls.jsonl").open("a",encoding="utf-8") as log:
        log.write(json.dumps({"endpoint":endpoint,"params":params},ensure_ascii=False)+"\\n")
    if (root/"slow_queries").exists():
        time.sleep(1)
    if (root/"quota_exhausted").exists():
        raise MapAPIError("高德额度达到限制，已暂停后续查询。",quota_exhausted=True)
    if endpoint=="geocode":
        address=params["address"]
        locations={"合成楼小区":"120.701,28.001", "大学城小区":"120.90,28.20"}
        if address=="同名小区":
            geocodes=[{"formatted_address":"温州市同名小区东区","location":"120.705,28.005","city":"温州市","level":"住宅区"},
                      {"formatted_address":"温州市同名小区西区","location":"120.806,28.106","city":"温州市","level":"住宅区"}]
        else:
            geocodes=[{"formatted_address":"温州市"+address,"location":locations[address],"city":"温州市","level":"住宅区"}]
        return {"status":"1","infocode":"10000","geocodes":geocodes}
    if endpoint=="search":
        return {"status":"1","infocode":"10000","pois":[{"name":"测试学校","address":"学院路","cityname":"温州市","adname":"鹿城区","location":"120.70,28.00"}]}
    minutes={"公共交通":45,"骑行":28,"步行":70,"驾车":16}
    payload={"duration":str(minutes[endpoint]*60),"distance":"6000"}
    if endpoint=="骑行":
        return {"errcode":0,"data":{"paths":[payload]}}
    return {"status":"1","infocode":"10000","route":{"transits" if endpoint=="公共交通" else "paths":[payload]}}
AmapClient._request=fake_request
if not (root/"qa_seeded").exists():
    origin=map_store.save_origin("学校",GeoPoint(lng=120.70,lat=28.00,city="温州市",label="学校",source="user"))
    map_store.save_origin("住处",GeoPoint(lng=120.90,lat=28.20,city="温州市",label="住处",source="user"))
    map_store.save_settings(map_store.MapSettings(default_city="温州市",active_origin_id=origin.id,persist_api_cache=True,
        credentials=MapCredentials(js_key="SYNTHETIC-JS-KEY",security_code="SYNTHETIC-SECURITY-CODE",web_key="SYNTHETIC-WEB-PRIVATE")))
    (root/"qa_seeded").write_text("synthetic test data only",encoding="utf-8")
if not st.session_state.get("qa_orders_seeded"):
    orders=[]
    for i,address in enumerate(["合成楼小区","大学城小区","同名小区"],1):
        orders.append(Order(order_id=f"ORD-MAP{i}",order_number=f"MAP{i}",raw_text=f"订单号 MAP{i} 小学数学 150/h 线下 周六晚上 每周1次 每次2小时 地址：{address}",
          grade="小学",subjects=["数学"],hourly_rate=150,mode="线下",days=[6],weekly_sessions=1,lesson_hours=2,time_slots=["晚上"],address=address,
          teacher_gender="不限",match_status="明确匹配",extraction_source="model"))
    st.session_state.orders=orders
    st.session_state.filtered=orders
    st.session_state.page="筛选结果"
    st.session_state._local_fields_revision=LOCAL_FIELDS_REVISION
    st.session_state.qa_orders_seeded=True
runpy.run_path(str(root/"app.py"),run_name="__main__")
'''


@pytest.fixture
def component_server(tmp_path):
    project, start, stop, log = start_isolated_server(tmp_path, COMPONENT_WRAPPER)
    try:
        yield start(), project
    finally:
        stop()
        log.close()


@pytest.fixture
def map_app_server(tmp_path):
    project, start, stop, log = start_isolated_server(tmp_path, APP_WRAPPER)
    try:
        yield start(), start, project
    finally:
        stop()
        log.close()


def api_calls(project):
    log = project / "map_calls.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def choose_selectbox(page, key, text):
    page.locator(f".st-key-{key}").get_by_role("combobox").click()
    page.get_by_role("option", name=text, exact=True).click()


def close_mobile_sidebar(page):
    sidebar = page.locator('[data-testid="stSidebar"]')
    button = sidebar.locator('[data-testid="stSidebarCollapseButton"] button')
    # A resize schedules React's responsive state update as well as a short
    # slide transition. Wait for that update before deciding to toggle it.
    page.wait_for_timeout(400)
    # On resize Streamlit may already be closing the sidebar. Its toggle is
    # still on screen during that animation; clicking it would reopen it.
    if sidebar.get_attribute("aria-expanded") == "true":
        try:
            button.click(timeout=3000)
        except BrowserTimeout:
            expect(sidebar).to_have_attribute("aria-expanded", "false")
    expect(sidebar).to_have_attribute("aria-expanded", "false")
    page.wait_for_function("""() => {
        const sidebar = document.querySelector('[data-testid="stSidebar"]');
        return !sidebar || sidebar.getBoundingClientRect().right <= 1;
    }""")


def test_app_batch_selection_and_fullwidth_mobile_results_with_mock_services(map_app_server):
    url, _, project = map_app_server
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        blocked = deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible(timeout=20000)
            expect(page.locator('.tutor-map-origin[role="img"]')).to_be_visible()
            expect(page.locator('.tutor-map-legend .tutor-map-origin svg')).to_be_visible()
            expect(page.locator('[class*="st-key-order_raw_desktop_"]').first).to_be_attached()
            expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
            expect(page.locator(".st-key-results_map")).to_have_count(1)
            map_box = page.locator(".st-key-results_map").bounding_box()
            list_box = page.locator(".st-key-results_list").bounding_box()
            assert map_box["width"] >= 300
            assert map_box["x"] + map_box["width"] <= list_box["x"]
            assert abs(map_box["y"] - list_box["y"]) <= 2
            expect(page.locator(".st-key-filter_panel")).to_have_count(0)
            expect(page.get_by_text("结果视图", exact=True)).to_have_count(0)
            expect(page.get_by_text("选择订单查看地图详情", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="定位筛选结果", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="开始定位", exact=True)).to_have_count(0)
            expect(page.get_by_text("本次定位完成。", exact=True)).to_be_visible(timeout=20000)
            expect(page.get_by_text("已定位 2 单 · 待确认 1 单", exact=False)).to_be_visible()
            # The second address finishes later and is far outside the first
            # point's viewport. Automatic markers must also become visible.
            bounds = page.evaluate("window.__amapAudit.maps.filter(map => !map.destroyed).at(-1).bounds")
            assert bounds["northeast"][0] >= 120.9 and bounds["northeast"][1] >= 28.2
            assert bounds["southwest"][0] <= 120.7 and bounds["southwest"][1] <= 28.0
            calls = api_calls(project)
            assert [call["endpoint"] for call in calls] == ["geocode", "geocode", "geocode", "search"]
            assert all(set(call["params"]) == {"address", "city"} for call in calls if call["endpoint"] == "geocode")
            assert "150" not in str(calls) and "SYNTHETIC-WEB-PRIVATE" not in str(calls)

            # Drafts now live on import. Editing and returning must reuse
            # resolved locations without issuing new location queries.
            page.get_by_role("button", name="修改筛选条件", exact=True).click()
            expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible()
            rate = page.locator(".st-key-filter_min_rate").get_by_role("spinbutton")
            rate.fill("100")
            rate.press("Enter")
            expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
            page.locator('[data-testid="stSidebar"]').get_by_text("筛选结果", exact=True).click()
            expect(page.locator(".tutor-map-pin").first).to_be_visible()
            assert len(api_calls(project)) == 4

            page.locator(".tutor-map-pin[data-order-id='ORD-MAP1']").first.click()
            expect(page.get_by_text("地图已选中", exact=False)).to_be_visible()
            page.get_by_role("button", name="查看订单", exact=True).hover()
            expect(page.get_by_role("tooltip")).to_contain_text("订单号 MAP1 小学数学 150/h")
            page.mouse.move(20, 20)
            expect(page.get_by_role("tooltip")).to_have_count(0)
            page.locator(".st-key-map_locate_ORD-MAP2").get_by_role("button").click()
            expect(page.locator(".tutor-map-pin[data-order-id='ORD-MAP2'].selected").first).to_be_visible()
            # Ambiguous candidates still remain in the list with unknown distance;
            # deleting the old inspector must not invent or auto-confirm a point.
            assert page.locator(".tutor-map-pin[data-order-id='ORD-MAP3']").count() == 0
            expect(page.get_by_text("已定位 2 单 · 待确认 1 单", exact=False)).to_be_visible()
            expect(page.get_by_text("通勤比较", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="确认订单位置", exact=True)).to_have_count(0)
            assert len(api_calls(project)) == 4
            page.reload()
            expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible()
            expect(page.locator(".tutor-map-pin[data-order-id='ORD-MAP1']").first).to_be_visible()
            expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
            assert len(api_calls(project)) == 4
            page.screenshot(path=str(ARTIFACTS / "app_desktop_offline_sdk.png"), full_page=True)

            page.set_viewport_size({"width": 390, "height": 844})
            close_mobile_sidebar(page)
            expect(page.locator(".tutor-map-shell")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.screenshot(path=str(ARTIFACTS / "app_mobile_map_offline_sdk.png"), full_page=True)
            expect(page.locator(".st-key-results_list")).to_be_visible()
            expect(page.locator(".st-key-map_locate_ORD-MAP1").get_by_role("button")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.screenshot(path=str(ARTIFACTS / "app_mobile_list.png"), full_page=True)
            assert not page.locator('[data-testid="stException"]').count()
            assert errors == []
            assert not any("restapi.amap.com" in value for value in blocked)
            assert b"SYNTHETIC-WEB-PRIVATE" not in (project / ".local" / "map_data.sqlite3").read_bytes()
        finally:
            context.close()
            browser.close()


def test_app_quota_failure_stops_batch_without_automatic_retry(map_app_server):
    url, _, project = map_app_server
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        try:
            (project / "quota_exhausted").write_text("synthetic quota limit", encoding="utf-8")
            page.goto(url)
            expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible(timeout=20000)
            expect(page.get_by_text("查询已暂停：", exact=False)).to_be_visible(timeout=15000)
            assert len(api_calls(project)) == 1
            page.get_by_role("button", name="修改筛选条件", exact=True).click()
            page.get_by_role("button", name="应用筛选", exact=True).click()
            expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible()
            expect(page.get_by_text("查询已暂停：", exact=False)).to_be_visible()
            assert len(api_calls(project)) == 1
            assert not page.locator('[data-testid="stException"]').count()
        finally:
            context.close()
            browser.close()


def test_cancelling_batch_keeps_completed_results_and_stops_future_calls(map_app_server):
    url, _, project = map_app_server
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        try:
            (project / "slow_queries").write_text("synthetic latency", encoding="utf-8")
            page.goto(url)
            expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible(timeout=20000)
            page.get_by_role("button", name="取消后续查询", exact=True).click()
            expect(page.get_by_text("已取消后续查询", exact=False)).to_be_visible(timeout=15000)
            count = len(api_calls(project))
            assert 0 < count < 3
            page.get_by_role("button", name="修改筛选条件", exact=True).click()
            page.get_by_role("button", name="应用筛选", exact=True).click()
            expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible()
            expect(page.get_by_text("已取消后续查询", exact=False)).to_be_visible()
            assert len(api_calls(project)) == count
            assert not page.locator('[data-testid="stException"]').count()
        finally:
            context.close()
            browser.close()


def test_component_selection_pick_and_safe_popup_with_offline_sdk(component_server):
    url, _ = component_server
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        blocked = deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            expect(page.locator(".tutor-map-pin").first).to_be_visible(timeout=20000)
            assert page.evaluate("window.__amapAudit.clusters.length > 0")
            page.locator(".tutor-map-pin").filter(has_text="MAP-101").click()
            expect(page.get_by_text("已选择 r1", exact=True)).to_be_visible()
            expect(page.locator(".tutor-map-pin.selected")).to_contain_text("MAP-101")
            page.locator(".tutor-map-pin").filter(has_text="MAP-102").click()
            expect(page.get_by_text("已选择 r2", exact=True)).to_be_visible()
            expect(page.locator(".tutor-map-popup")).to_contain_text("<img src=x")
            assert page.locator(".tutor-map-popup img").count() == 0
            assert page.evaluate("window.__injected !== true")
            map_count = page.evaluate("window.__amapAudit.maps.length")
            page.get_by_text("地图选点模式", exact=True).click()
            expect(page.locator(".tutor-map-pick-hint")).to_be_visible()
            assert page.evaluate("window.__amapAudit.maps.length") == map_count
            page.locator(".tutor-map-canvas").click(position={"x": 600, "y": 400})
            expect(page.get_by_text("已选坐标 120.701,28.001", exact=True)).to_be_visible()
            assert not page.locator('[data-testid="stException"]').count()
            assert errors == []
            assert not any("restapi.amap.com" in url for url in blocked)
            page.screenshot(path=str(ARTIFACTS / "component_offline_sdk.png"), full_page=True)
        finally:
            context.close()
            browser.close()
