"""离线浏览器验证房屋图例、匹配边框及独立的定位精度说明。"""
import pytest
from playwright.sync_api import expect, sync_playwright

from map_browser_support import ROOT, deny_external_requests
from test_map_browser import SDK


ARTIFACTS = ROOT / "artifacts" / "acceptance" / "map_legend"


@pytest.mark.parametrize("width", [390, 1280])
def test_legend_matches_markers_at_desktop_and_mobile_widths(width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 760})
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.set_content('<div id="host"><div class="tutor-map-slot"></div></div>')
            page.add_style_tag(content=(ROOT / "tutor_screen/map_frontend.css").read_text(encoding="utf-8"))
            source = (ROOT / "tutor_screen/map_frontend.js").read_text(encoding="utf-8")
            page.add_script_tag(content=source.replace("export default function", "window.renderLegendMap = function"))
            page.evaluate('''() => {
              const points = [];
              for (const status of ['明确匹配', '可能匹配']) {
                for (const precision of ['exact', 'approximate', 'area']) {
                  const id = String(points.length);
                  points.push({id, number: id, lng: 120.7 + points.length * .01, lat: 28,
                    status, precision, area_scale: 'large'});
                }
              }
              window.renderLegendMap({parentElement: document.querySelector('#host'),
                setTriggerValue: () => {}, data: {points, height: 600,
                  origin: {lng: 120.69, lat: 28, label: '学校'},
                  js_key: 'synthetic', security_code: 'synthetic'}});
            }''')
            legend = page.locator(".tutor-map-legend")
            expect(page.locator(".tutor-map-pin").first).to_be_visible()
            expect(legend).to_contain_text("明确匹配（实线）")
            expect(legend).to_contain_text("可能匹配（虚线）")
            expect(legend).to_contain_text("约大致定位")
            origin = legend.locator(".tutor-map-origin")
            expect(origin.locator("svg path")).to_have_count(1)
            assert origin.bounding_box()["width"] >= 22
            assert origin.locator("path").get_attribute("d") == page.locator('.tutor-map-origin[role="img"] path').get_attribute("d")
            expect(legend.locator(".tutor-map-legend-dot:not(.possible):not(.approximate)")).to_have_css("border-top-style", "solid")
            expect(legend.locator(".tutor-map-legend-dot.possible")).to_have_css("border-top-style", "dashed")
            for index in range(6):
                marker = page.locator(f'.tutor-map-pin[data-order-id="{index}"]').first
                expect(marker).to_have_css("border-top-style", "solid" if index < 3 else "dashed")
                assert marker.inner_text().startswith("约") == (index % 3 != 0)
                marker.click()
                expect(page.locator(".tutor-map-pin.selected")).to_have_css("border-top-style", "solid" if index < 3 else "dashed")
            page.evaluate('''() => {
              const cluster = window.__amapAudit.clusters.at(-1);
              const marker = new AMap.Marker({map: cluster.map});
              cluster.options.renderClusterMarker({marker, data: cluster.data, count: 6});
            }''')
            expect(page.locator(".tutor-map-cluster")).to_have_css("border-top-style", "dashed")
            expect(page.locator(".tutor-map-cluster")).to_contain_text("约 · 6")
            page.evaluate('''() => {
              const cluster = window.__amapAudit.clusters.at(-1);
              const marker = new AMap.Marker({map: cluster.map});
              cluster.options.renderClusterMarker({marker, data: cluster.data.slice(0, 3), count: 3});
            }''')
            expect(page.locator(".tutor-map-cluster").last).to_have_css("border-top-style", "solid")
            expect(page.locator(".tutor-map-cluster").last).to_contain_text("约 · 3")
            bounds = legend.bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ARTIFACTS / f"legend_{width}.png"), full_page=True)
        finally:
            context.close()
            browser.close()
