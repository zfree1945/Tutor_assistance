"""Offline acceptance for the map card's action row and unchanged controls."""
import csv
import io

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright

from map_browser_support import ROOT, deny_external_requests, start_isolated_server
from test_map_browser import APP_WRAPPER, SDK, close_mobile_sidebar


LABELS = ["修改筛选条件", "导出当前筛选 CSV", "导出明确匹配 CSV"]
# Native controls outside the card provide an independent size reference using
# the same theme and labels. They deliberately have no application callbacks.
WRAPPER = APP_WRAPPER + '''
if st.session_state.page == "筛选结果":
    with st.container(key="qa_native_controls"):
        st.button("修改筛选条件", key="qa_native_edit")
        with st.container(horizontal=True, width="content", wrap=False):
            st.download_button("导出当前筛选 CSV", b"reference", key="qa_native_current", on_click="ignore")
            st.download_button("导出明确匹配 CSV", b"reference", key="qa_native_exact", on_click="ignore")
'''


@pytest.fixture
def actions_server(tmp_path, request):
    wrapper = WRAPPER
    if getattr(request, "param", None) == "empty":
        wrapper = wrapper.replace(
            'runpy.run_path(str(root/"app.py"),run_name="__main__")',
            'st.session_state.orders=[]\nst.session_state.filtered=[]\n'
            'runpy.run_path(str(root/"app.py"),run_name="__main__")',
        )
    _, start, stop, log = start_isolated_server(tmp_path, wrapper)
    try:
        yield start()
    finally:
        stop()
        log.close()


def _control_metrics(button):
    return button.evaluate("""el => {
      const style = getComputedStyle(el);
      const labelStyle = getComputedStyle(el.querySelector('p'));
      const box = el.getBoundingClientRect();
      return {width:box.width, height:box.height, fontSize:labelStyle.fontSize,
              padding:style.padding, borderRadius:style.borderRadius};
    }""")


@pytest.mark.parametrize("width,height", [(1440, 1000), (1920, 1080), (390, 844)])
def test_actions_end_map_card_in_order_without_resizing(actions_server, tmp_path, width, height):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": height}, accept_downloads=True)
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(actions_server)
            card = page.locator(".st-key-results_map")
            actions = card.locator(".st-key-results_actions")
            expect(actions).to_be_attached(timeout=25000)
            if width < 640:
                close_mobile_sidebar(page)
            expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
            expect(actions.get_by_role("button")).to_have_text(LABELS)
            # The card ends with this row, after all map status/tool controls.
            assert actions.evaluate("el => el === el.parentElement.lastElementChild")
            assert not page.locator(".st-key-results_list").get_by_role("button", name="导出", exact=False).count()
            expect(page.locator(".st-key-edit_filters")).to_have_count(1)
            references = page.locator(".st-key-qa_native_controls")
            boxes = []
            for label in LABELS:
                control = actions.get_by_role("button", name=label, exact=True)
                reference = references.get_by_role("button", name=label, exact=True)
                actual, original = _control_metrics(control), _control_metrics(reference)
                assert actual == original, (label, actual, original)
                boxes.append(control.bounding_box())
            assert max(box["y"] for box in boxes) - min(box["y"] for box in boxes) <= 1
            assert boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"]
            assert abs(boxes[0]["x"] - actions.bounding_box()["x"]) <= 1
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            artifact = ROOT / "artifacts" / "acceptance" / "results_actions"
            artifact.mkdir(parents=True, exist_ok=True)
            actions.scroll_into_view_if_needed()
            page.screenshot(path=str(artifact / f"actions_{width}.png"))
            exports = []
            for label, filename in zip(LABELS[1:], ["家教订单_当前筛选.csv", "家教订单_明确匹配.csv"]):
                with page.expect_download() as download_info:
                    actions.get_by_role("button", name=label, exact=True).click()
                download = download_info.value
                assert download.suggested_filename == filename
                saved = tmp_path / filename
                download.save_as(saved)
                content = saved.read_text(encoding="utf-8-sig")
                exports.append(list(csv.DictReader(io.StringIO(content))))
                expect(actions).to_be_attached()
            assert {row["order_id"] for row in exports[0]} == {"ORD-MAP1", "ORD-MAP2", "ORD-MAP3"}
            assert exports[1] == [row for row in exports[0] if row["match_status"] == "明确匹配"]
            actions.get_by_role("button", name=LABELS[0], exact=True).click()
            expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible()
            assert not errors
            expect(page.locator('[data-testid="stException"]')).to_have_count(0)
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("actions_server", ["empty"], indirect=True)
def test_empty_results_keep_all_three_actions_available(actions_server, tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        try:
            page.goto(actions_server)
            actions = page.locator(".st-key-results_map .st-key-results_actions")
            expect(actions.get_by_role("button")).to_have_text(LABELS, timeout=25000)
            for index, label in enumerate(LABELS[1:]):
                with page.expect_download() as download_info:
                    actions.get_by_role("button", name=label, exact=True).click()
                saved = tmp_path / f"empty_{index}.csv"
                download_info.value.save_as(saved)
                assert list(csv.DictReader(io.StringIO(saved.read_text(encoding="utf-8-sig")))) == []
            actions.get_by_role("button", name=LABELS[0], exact=True).click()
            expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible()
            expect(page.locator('[data-testid="stException"]')).to_have_count(0)
        finally:
            context.close()
            browser.close()
