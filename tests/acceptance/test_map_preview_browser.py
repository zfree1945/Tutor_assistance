"""Hover previews stay beside the selected marker without sending text to SDK."""
import pytest
from playwright.sync_api import expect, sync_playwright

from map_browser_support import ROOT, deny_external_requests, start_isolated_server
from test_map_browser import COMPONENT_WRAPPER, SDK


ARTIFACTS = ROOT / "artifacts/acceptance/map_preview"
RAW = "MAP-101 原始订单\n地址：合成小区\n对象：初一学生\n要求：耐心负责"
RAW_TWO = "MAP-102 原始订单\n<img src=x onerror=window.__rawInjected=true>\n" + "详细教学要求。" * 1200
WRAPPER = COMPONENT_WRAPPER.replace(
    "result = render_map(",
    f"points[0]['raw_text'] = {RAW!r}\npoints[1]['raw_text'] = {RAW_TWO!r}\nresult = render_map(",
).replace(
    'selected_id=st.session_state.get("test_selected"), picking=picking,',
    'selected_id=st.session_state.get("test_selected"), picking=picking, focus_token=st.session_state.get("preview_focus", 0),',
).replace(
    'if result.selected:\n',
    'if result.selected:\n    st.session_state.preview_focus = st.session_state.get("preview_focus", 0) + 1\n',
)


@pytest.fixture
def preview_server(tmp_path):
    _, start, stop, log = start_isolated_server(tmp_path, WRAPPER)
    try:
        yield start()
    finally:
        stop()
        log.close()


def test_hover_preview_survives_click_rerun_and_closes_only_after_leaving(preview_server):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(preview_server)
            page.locator('.tutor-map-pin[data-order-id="r1"]').click(timeout=25000)
            expect(page.get_by_text("已选择 r1", exact=True)).to_be_visible()
            view = page.get_by_role("button", name="查看订单", exact=True)
            tooltip = page.get_by_role("tooltip")
            expect(tooltip).to_have_count(0)
            view.hover()
            expect(tooltip.locator(".tutor-map-raw-text")).to_have_text(RAW)
            marker = page.locator(".tutor-map-pin.selected").bounding_box()
            box = tooltip.bounding_box()
            assert abs(box["x"] - marker["x"] - marker["width"] - 12) <= 1
            assert abs(box["y"] + box["height"] / 2 - marker["y"] - marker["height"] / 2) <= 1
            # No timeout or movement within the button may dismiss the text.
            page.wait_for_timeout(1200)
            view.hover(position={"x": 8, "y": 8})
            expect(tooltip).to_be_visible()
            view.evaluate("el => { el.focus(); el.blur(); }")
            expect(tooltip).to_be_visible()
            markers_before = page.evaluate("window.__amapAudit.markers.length")
            center_before = page.evaluate("window.__amapAudit.maps.at(-1).center")
            view.click()
            page.wait_for_function("count => window.__amapAudit.markers.length > count", arg=markers_before)
            expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
            expect(tooltip).to_be_visible()
            assert page.evaluate("window.__amapAudit.maps.at(-1).center") == center_before
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ARTIFACTS / "raw_preview_right_of_marker.png"))
            page.mouse.move(20, 20)
            expect(tooltip).to_have_count(0)
            view.hover()
            expect(tooltip).to_be_visible()
            page.evaluate("window.__amapAudit.info.at(-1).close()")
            expect(tooltip).to_have_count(0)
            page.locator('.tutor-map-pin[data-order-id="r2"]').click()
            expect(page.get_by_text("已选择 r2", exact=True)).to_be_visible()
            view.hover()
            expect(tooltip.locator(".tutor-map-raw-text")).to_have_text(RAW_TWO)
            assert tooltip.locator("img").count() == 0
            assert page.evaluate("window.__rawInjected !== true")
            page.mouse.wheel(0, 500)
            expect(tooltip).to_be_visible()
            assert tooltip.evaluate("el => el.scrollTop") > 0
            assert page.evaluate("window.__amapAudit.clusters.every(c => c.data.every(p => Object.keys(p).sort().join(',') === 'lnglat,orderId'))")
            page.mouse.move(20, 20)
            expect(tooltip).to_have_count(0)
            page.set_viewport_size({"width": 390, "height": 844})
            view.hover()
            expect(tooltip).to_be_visible()
            mobile = tooltip.bounding_box()
            assert mobile["x"] >= 11 and mobile["x"] + mobile["width"] <= 379
            assert mobile["y"] >= 11 and mobile["y"] + mobile["height"] <= 833
            assert not errors
        finally:
            context.close()
            browser.close()
