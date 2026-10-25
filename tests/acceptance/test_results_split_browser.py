"""原文默认折叠、四单手动展开、宽地图及手机原排版的离线验收。"""
import pytest
from playwright.sync_api import expect, sync_playwright

from map_browser_support import ROOT, deny_external_requests, start_isolated_server
from test_map_browser import APP_WRAPPER, SDK, close_mobile_sidebar


ARTIFACTS = ROOT / "artifacts/acceptance/results_split"
SEED = '''if not st.session_state.get("qa_split_orders"):
    from tutor_screen.maps import GeoResult
    orders = []
    for i in range(1, 8):
        raw = f"订单号 SIX{i}\\n年级科目：小学数学\\n地址：八仙楼小区\\n时间：周六晚上，每周1次，每次2小时\\n报酬：150/h\\n要求：男女不限\\n备注：" + "细心讲解，巩固知识。" * 80
        orders.append(st.session_state.orders[0].model_copy(deep=True, update={
            "order_id": f"SIX{i}", "order_number": f"SIX{i}", "raw_text": raw}))
    st.session_state.orders = orders
    st.session_state.filtered = orders
    map_store.save_geo("八仙楼小区", "温州市", GeoResult(status="located", resolver_revision=3,
        candidates=[GeoPoint(lng=120.701, lat=28.001, city="温州市", precision="exact")]))
    st.session_state._parse_summary = "解析完成：共 7 条，成功 7 条，失败 0 条"
    st.session_state.qa_split_orders = True
'''
WRAPPER = APP_WRAPPER.replace('runpy.run_path(str(root/"app.py"),run_name="__main__")',
                              SEED + '\nrunpy.run_path(str(root/"app.py"),run_name="__main__")')


@pytest.fixture
def split_server(tmp_path):
    _, start, stop, log = start_isolated_server(tmp_path, WRAPPER)
    try:
        yield start()
    finally:
        stop()
        log.close()


@pytest.mark.parametrize("width,height", [(1920, 1080), (1440, 1000)])
def test_raw_orders_start_collapsed_and_four_manually_opened_orders_fit(split_server, width, height):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": height})
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(split_server)
            cards = page.locator('[role="tabpanel"]:visible [class*="st-key-order_card_"]')
            expect(cards).to_have_count(7, timeout=25000)
            expect(page.locator('[class*="st-key-order_raw_desktop_"]').first).to_be_attached(timeout=15000)
            # The browser reports its breakpoint on mount. Wait for the
            # resulting rerun to remove Streamlit's stale previous containers.
            expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
            expect(page.locator(".st-key-results_map")).to_have_count(1)
            expect(page.locator(".st-key-results_list")).to_have_count(1)
            expect(cards.locator("details[open]")).to_have_count(0)
            for i in range(4):
                cards.nth(i).locator("summary").click()
            expect(cards.locator("details[open]")).to_have_count(4)
            boxes = [cards.nth(i).bounding_box() for i in range(4)]
            assert boxes[0]["x"] < boxes[1]["x"]
            assert abs(boxes[0]["y"] - boxes[1]["y"]) <= 2
            assert abs(boxes[2]["y"] - boxes[3]["y"]) <= 2
            assert boxes[2]["y"] > boxes[0]["y"]
            assert max(box["y"] + box["height"] for box in boxes) <= height, boxes
            raw = cards.first.locator('[data-testid="stText"]')
            assert raw.evaluate("el => el.scrollHeight > el.clientHeight")
            assert raw.evaluate('''el => {
              const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
              const text = walker.nextNode();
              const range = document.createRange();
              range.setStart(text, 0); range.setEnd(text, 1);
              return range.getBoundingClientRect().top >= el.getBoundingClientRect().top - 1;
            }''')
            assert raw.inner_text().count("细心讲解，巩固知识。") == 80
            assert raw.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 14
            assert page.evaluate("Number(getComputedStyle(document.body).zoom) || 1") == 1
            assert page.get_by_role("button", name="下一页", exact=True).count() == 0
            map_box = page.locator(".st-key-results_map").bounding_box()
            assert map_box["x"] + map_box["width"] <= boxes[0]["x"]
            workspace = page.locator(".st-key-results_workspace").bounding_box()
            assert map_box["width"] >= workspace["width"] * .4
            expect(page.locator('.st-key-results_map').get_by_text("解析完成：共 7 条，成功 7 条，失败 0 条", exact=True)).to_be_visible()
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ARTIFACTS / f"four_open_desktop_{width}.png"))
            page.locator('.tutor-map-pin[data-order-id="SIX7"]').first.click()
            expect(page.get_by_text("地图已选中", exact=False)).to_be_visible()
            for i in range(4):
                cards.nth(i).locator("summary").click()
            expect(cards.locator("details[open]")).to_have_count(0)
            page.set_viewport_size({"width": 390, "height": 844})
            close_mobile_sidebar(page)
            expect(cards.first.locator("details")).not_to_have_attribute("open", "", timeout=15000)
            expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
            expect(page.locator(".st-key-results_map")).to_have_count(1)
            mobile_map = page.locator(".st-key-results_map").bounding_box()
            mobile_list = page.locator(".st-key-results_list").bounding_box()
            assert mobile_map["y"] + mobile_map["height"] <= mobile_list["y"]
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.set_viewport_size({"width": width, "height": height})
            expect(page.locator('[class*="st-key-order_raw_desktop_"]').first).to_be_attached(timeout=15000)
            expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
            expect(page.locator(".st-key-results_map")).to_have_count(1)
            expect(cards).to_have_count(7)
            expect(cards.locator("details[open]")).to_have_count(0)
            assert not errors and page.locator('[data-testid="stException"]').count() == 0
        finally:
            context.close()
            browser.close()
