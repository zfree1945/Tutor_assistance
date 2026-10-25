"""Visual acceptance of the shared five-page UI, using synthetic local data.

The screenshots exercise the actual Streamlit DOM and theme, not a mock HTML
page. Real API calls and real local credentials/history are never used.
"""
import json
from uuid import uuid4

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright

from map_browser_support import ROOT, deny_external_requests, start_isolated_server
from test_map_browser import APP_WRAPPER, SDK, api_calls, close_mobile_sidebar


ARTIFACTS = ROOT / "artifacts" / "acceptance" / f"ui_redesign_qa_{uuid4().hex[:8]}"

# The normal offline wrapper seeds orders and the app records them in the
# temporary history database. Pre-cache two synthetic points for a useful map
# screenshot without running the batch or calling any external service.
_SEED = '''if not st.session_state.get("qa_redesign_seeded"):
    from tutor_screen.maps import GeoResult
    from tutor_screen.models import Criteria
    from tutor_screen.history_store import create_import, append_event
    st.session_state.api_key="SYNTHETIC-MODEL-NOT-A-KEY"
    for address,lng,lat in [("八仙楼小区",120.701,28.001),("大学城小区",120.90,28.20)]:
        map_store.save_geo(address,"温州市",GeoResult(status="located",candidates=[GeoPoint(lng=lng,lat=lat,label="温州市"+address,city="温州市")]))
    map_store.save_geo("同名小区","温州市",GeoResult(status="ambiguous",candidates=[GeoPoint(lng=120.705,lat=28.005,city="温州市")],resolver_revision=3))
    st.session_state.orders[1].match_status="可能匹配"
    st.session_state.orders[1].uncertain_fields=["报酬补充说明"]
    st.session_state.filtered=st.session_state.orders
    st.session_state.current_import_id=create_import(source_type="粘贴",input_text="\\n\\n".join(o.raw_text for o in st.session_state.orders))
    append_event(st.session_state.current_import_id,kind="解析",criteria=Criteria(),orders=st.session_state.orders,parsed_orders=st.session_state.orders)
    st.session_state.order_text="\\n\\n".join(o.raw_text for o in st.session_state.orders)
    st.session_state.qa_redesign_seeded=True
'''
REDESIGN_WRAPPER = APP_WRAPPER.replace(
    'runpy.run_path(str(root/"app.py"),run_name="__main__")',
    _SEED + '\nrunpy.run_path(str(root/"app.py"),run_name="__main__")',
)


@pytest.fixture
def redesign_server(tmp_path):
    project, start, stop, log = start_isolated_server(tmp_path, REDESIGN_WRAPPER)
    try:
        yield start(), project
    finally:
        stop()
        log.close()


def _no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.locator('[data-testid="stMain"]').evaluate("el => el.scrollWidth <= el.clientWidth + 1")


def _is_blue(color):
    # Check the rendered UI, not just the theme source; a stale/red Streamlit
    # primary color must be caught even if the config file looks correct.
    channels = [int(value.strip()) for value in color.removeprefix("rgb(").removesuffix(")").split(",")]
    red, green, blue = channels
    return blue > 150 and blue > red * 2 and green > red


def _capture_page(page, destination):
    """Streamlit scrolls its main pane, so capture its full content height."""
    expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
    viewport = page.viewport_size.copy()
    height = page.locator('[data-testid="stMainBlockContainer"]').evaluate("el => Math.ceil(el.getBoundingClientRect().height) + 40")
    page.set_viewport_size({"width": viewport["width"], "height": max(viewport["height"], height)})
    if viewport["width"] <= 640:
        close_mobile_sidebar(page)
        expect(page.locator('[data-testid="stSidebar"]')).to_have_attribute("aria-expanded", "false")
    page.locator('[data-testid="stMain"]').evaluate("el => el.scrollTop = 0")
    page.screenshot(path=str(destination), full_page=True, animations="disabled")
    page.set_viewport_size(viewport)


def test_all_five_pages_share_desktop_and_mobile_theme(redesign_server):
    url, project = redesign_server
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    assert (project / ".streamlit" / "config.toml").exists()
    assert not (project / ".streamlit" / "secrets.toml").exists()
    specifications = [
        ("筛选结果", "results", ["results_map", "results_list"], None),
        ("模型设置", "model", ["model_connection", "model_help"], "测试连接"),
        ("地图设置", "map", ["map_credentials", "map_origins", "map_cache"], "保存地图设置"),
        ("订单导入", "import", ["import_editor", "filter_panel"], "应用筛选"),
        ("开发日志", "history", ["history_overview", "history_record", "history_snapshot"], None),
    ]
    measurements = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        blocked = deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible(timeout=25000)
            expect(page.locator(".tutor-map-pin").first).to_be_visible()
            for title, name, sections, primary_action in specifications:
                page.set_viewport_size({"width": 1440, "height": 1000})
                # Let Streamlit finish its responsive sidebar update before
                # interacting with the expand button from the previous width.
                page.wait_for_timeout(400)
                sidebar = page.locator('[data-testid="stSidebar"]')
                # Streamlit remembers a mobile collapse when returning to desktop.
                if sidebar.get_attribute("aria-expanded") == "false":
                    page.locator('[data-testid="stExpandSidebarButton"]').click()
                expect(sidebar).to_have_attribute("aria-expanded", "true")
                sidebar.get_by_text(title, exact=True).click()
                expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                expect(sidebar.get_by_role("radio", name=title, exact=True)).to_be_checked()
                for key in sections:
                    expect(page.locator(f".st-key-{key}")).to_be_visible()
                if name == "import":
                    for key in ["step_settings", "step_import", "step_results", "clear_orders", "start_processing"]:
                        expect(page.locator(f".st-key-{key}")).to_have_count(0)
                    editor = page.locator(".st-key-import_editor").bounding_box()
                    filters = page.locator(".st-key-filter_panel").bounding_box()
                    assert editor["y"] + editor["height"] <= filters["y"]
                    assert page.locator(".st-key-import_editor .st-key-filter_panel").count() == 0
                elif name == "results":
                    expect(page.locator(".st-key-filter_panel")).to_have_count(0)
                elif name == "model":
                    provider = page.locator(".st-key-provider").get_by_role("combobox")
                    expect(provider).to_be_disabled()
                    expect(provider).to_have_value("DeepSeek")
                    help_card = page.locator(".st-key-model_help")
                    heading = help_card.get_by_text("01 · 快速创建deepseek API", exact=True)
                    shortcut = help_card.get_by_role("link", name="前往 DeepSeek 创建 API Key", exact=True)
                    expect(shortcut).to_have_attribute("href", "https://platform.deepseek.com/api_keys")
                    assert shortcut.bounding_box()["y"] > heading.bounding_box()["y"]
                _no_overflow(page)
                assert not page.locator('[data-testid="stException"]').count()
                if name == "history":
                    # Native metric/JSON chunks are lazy loaded; don't capture
                    # skeletons as though they were the final page.
                    expect(page.locator('[data-testid="stMetricValue"]').first).to_have_text("1")
                    expect(page.locator('[data-testid="stJson"]').first).to_be_visible()
                measurements[name] = {
                    "desktop_width": page.locator(".block-container").bounding_box()["width"],
                    "sidebar_background": sidebar.evaluate("el => getComputedStyle(el).backgroundColor"),
                }
                assert measurements[name]["desktop_width"] > 1100
                if primary_action:
                    action = page.get_by_role("button", name=primary_action, exact=True)
                    color = action.evaluate("el => getComputedStyle(el).backgroundColor")
                    assert _is_blue(color), (title, color)
                    measurements[name]["primary_background"] = color
                _capture_page(page, ARTIFACTS / f"{name}_desktop.png")

                page.set_viewport_size({"width": 390, "height": 844})
                close_mobile_sidebar(page)
                expect(sidebar).to_have_attribute("aria-expanded", "false")
                _no_overflow(page)
                for key in sections:
                    expect(page.locator(f".st-key-{key}")).to_be_visible()
                if name == "model":
                    expect(page.locator(".st-key-model_help").get_by_role("link", name="前往 DeepSeek 创建 API Key")).to_be_visible()
                _capture_page(page, ARTIFACTS / f"{name}_mobile.png")
            assert api_calls(project) == []
            assert not any("restapi.amap.com" in value for value in blocked)
            assert errors == []
            (ARTIFACTS / "measurements.json").write_text(json.dumps(measurements, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            context.close()
            browser.close()


def test_result_hierarchy_and_marker_card_linkage_without_removed_inspector(redesign_server):
    url, project = redesign_server
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        try:
            page.goto(url)
            expect(page.locator(".tutor-map-pin").first).to_be_visible(timeout=25000)
            expect(page.locator('[class*="st-key-order_raw_desktop_"]').first).to_be_attached()
            expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
            expect(page.locator(".st-key-results_map")).to_have_count(1)
            map_panel = page.locator(".st-key-results_map").bounding_box()
            orders = page.locator(".st-key-results_list").bounding_box()
            assert map_panel["width"] >= 300
            assert page.locator(".tutor-map-shell").bounding_box()["height"] >= 550
            assert map_panel["x"] + map_panel["width"] <= orders["x"]
            assert abs(map_panel["y"] - orders["y"]) <= 2
            expect(page.locator(".st-key-filter_panel")).to_have_count(0)
            for removed in ["结果视图", "选择订单查看地图详情", "确认或修正位置", "恢复自动定位", "通勤比较"]:
                expect(page.get_by_text(removed, exact=True)).to_have_count(0)
            expect(page.locator(".tutor-map-pin[data-order-id='ORD-MAP1']").first).to_be_visible()
            expect(page.locator(".tutor-map-pin[data-order-id='ORD-MAP2']").first).to_be_visible()
            page.locator(".tutor-map-pin[data-order-id='ORD-MAP2']").first.click()
            expect(page.get_by_text("地图已选中", exact=False)).to_be_visible()
            expect(page.get_by_role("tab", name="可能匹配（1）", exact=True)).to_have_attribute("aria-selected", "true")
            assert api_calls(project) == []
            assert not page.locator('[data-testid="stException"]').count()
        finally:
            context.close()
            browser.close()
