"""真实浏览器中的区域标记、弹窗、卡片联动与窄屏；使用离线高德SDK。"""
from pathlib import Path
import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright

from map_browser_support import ROOT, deny_external_requests, start_isolated_server
from test_map_browser import SDK, api_calls, close_mobile_sidebar
from test_ui_redesign_browser import REDESIGN_WRAPPER


ARTIFACTS = ROOT / "artifacts" / "acceptance" / f"map_area_qa_{uuid4().hex[:8]}"
_AREA_SEED = '''if not st.session_state.get("qa_area_seeded"):
    from tutor_screen.maps import GeoResult, LOCATION_REVISION
    map_store.save_geo("同名小区","温州市",GeoResult(status="ambiguous",resolver_revision=LOCATION_REVISION,
        area_point=GeoPoint(lng=120.8,lat=28.1,city="温州市",label="合成街道人民路",precision="area")))
    st.session_state.qa_area_seeded=True
'''
WRAPPER = REDESIGN_WRAPPER.replace('runpy.run_path(str(root/"app.py"),run_name="__main__")',
                                   _AREA_SEED + '\nrunpy.run_path(str(root/"app.py"),run_name="__main__")')


@pytest.fixture
def area_server(tmp_path):
    project, start, stop, log = start_isolated_server(tmp_path, WRAPPER)
    try:
        yield start(), project
    finally:
        stop()
        log.close()


def test_area_pin_popup_card_focus_and_mobile_layout(area_server):
    url, project = area_server
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            marker = page.locator(".tutor-map-pin.area[data-order-id='ORD-MAP3']").first
            expect(marker).to_be_visible(timeout=25000)
            legend = page.locator(".tutor-map-legend")
            expect(legend.locator(".tutor-map-origin svg")).to_be_visible()
            expect(legend.locator(".tutor-map-legend-dot.possible")).to_have_css("border-top-style", "dashed")
            expect(marker).to_contain_text("约 · MAP3")
            assert marker.evaluate("el => getComputedStyle(el).borderTopStyle") == "solid"
            expect(page.get_by_text("区域约略标记 1 单", exact=False)).to_be_visible()
            marker.click()
            popup = page.locator(".tutor-map-popup")
            expect(popup).to_contain_text("约略位置：合成街道人民路")
            expect(popup).to_contain_text("实际距离待确认")
            expect(page.get_by_text("地图已选中", exact=False)).to_be_visible()
            assert api_calls(project) == []
            page.screenshot(path=str(ARTIFACTS / "area_desktop_offline_sdk.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            close_mobile_sidebar(page)
            expect(page.locator(".tutor-map-pin.area").first).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.screenshot(path=str(ARTIFACTS / "area_mobile_offline_sdk.png"), full_page=True)
            assert not page.locator('[data-testid="stException"]').count()
            assert errors == [] and api_calls(project) == []
        finally:
            context.close()
            browser.close()


def test_area_scales_render_styles_popups_and_mixed_cluster(tmp_path):
    assets = ROOT / "tutor_screen"
    source = (assets / "map_frontend.js").read_text(encoding="utf-8")
    css = (assets / "map_frontend.css").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1100, "height": 700})
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.set_content('<div id="host"><div class="tutor-map-slot"></div></div>')
            page.add_style_tag(content=css)
            page.add_script_tag(content=source.replace("export default function", "window.renderAreaMap = function"))
            page.evaluate('''() => window.renderAreaMap({
              parentElement: document.querySelector('#host'), setTriggerValue: () => {},
              data: {js_key: 'synthetic', security_code: 'synthetic', height: 560,
                points: ['small', 'medium', 'large', null].map((scale, i) => ({
                  id: 'scale-' + i, number: String(i + 1), lng: 120.7 + i * .01, lat: 28,
                  precision: 'area', ...(scale ? {area_scale: scale} : {}), location_label: '示例位置'
                }))}
            })''')
            descriptions = ["附近地标参考，范围较小", "道路或社区参考位置", "范围较大，仅供参考所在片区", "道路或社区参考位置"]
            for index, scale in enumerate(["small", "medium", "large", "medium"]):
                marker = page.locator(f'.tutor-map-pin[data-order-id="scale-{index}"]').first
                expect(marker).to_be_visible()
                expect(marker).to_have_class(re.compile(rf"\barea-{scale}\b"))
                expect(marker).to_contain_text(f"约 · {index + 1}")
                marker.click()
                expect(page.locator(".tutor-map-popup")).to_contain_text(descriptions[index])
            opacity = [page.locator(f'.tutor-map-pin.area-{scale}').first.evaluate("el => Number(getComputedStyle(el).opacity)") for scale in ["small", "medium", "large"]]
            assert opacity[0] > opacity[1] > opacity[2]
            expect(page.locator('.tutor-map-pin[data-order-id="scale-1"]').first).to_have_css("border-top-style", "solid")
            page.evaluate('''() => {
              const cluster = window.__amapAudit.clusters.at(-1);
              const marker = new AMap.Marker({map: cluster.map});
              cluster.options.renderClusterMarker({marker, data: cluster.data, count: 4});
            }''')
            expect(page.locator(".tutor-map-cluster.area-large")).to_contain_text("约 · 4")
            assert errors == []
            page.screenshot(path=str(tmp_path / "area_scales.png"), full_page=True)
        finally:
            context.close()
            browser.close()
