"""One failed eighth address must not block a 20-address batch, offline."""
import pytest
import sqlite3
from playwright.sync_api import expect, sync_playwright

from map_browser_support import ROOT, deny_external_requests, start_isolated_server
from test_map_browser import APP_WRAPPER, SDK, api_calls


ARTIFACTS = ROOT / "artifacts/acceptance/map_batch_failure"
WRAPPER = APP_WRAPPER.replace(
    '["合成楼小区","大学城小区","同名小区"]',
    '[f"合成小区{i}" for i in range(1,21)]',
).replace(
    '        locations={',
    '''        if address == "合成小区8" and not (root/"retry_succeeds").exists():
            raise MapAPIError("高德服务繁忙，此地址暂未完成定位。",stop_batch=False,code="10016",endpoint="geocode")
        if address.startswith("合成小区"):
            return {"status":"1","geocodes":[{"formatted_address":address,"location":"120.701,28.001","city":"温州市","level":"门牌号"}]}
        locations={''',
)


def test_failed_address_is_skipped_and_only_explicit_retry_queries_it_again(tmp_path):
    project, start, stop, log = start_isolated_server(tmp_path, WRAPPER)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        deny_external_requests(context, sdk_source=SDK)
        page = context.new_page()
        try:
            page.goto(start())
            expect(page.get_by_text("已查询 20 / 20 个地址", exact=True)).to_be_visible(timeout=60000)
            expect(page.get_by_text("本次定位已结束：", exact=False)).to_be_visible()
            expect(page.get_by_text("查询已暂停：", exact=False)).to_have_count(0)
            page.get_by_text("定位失败（1）", exact=True).click()
            expect(page.get_by_text("合成小区8 ·", exact=False)).to_contain_text("10016")
            assert len(api_calls(project)) == 20
            with sqlite3.connect(project / ".local/order_history.sqlite3") as db:
                notes = "\n".join(row[0] for row in db.execute("SELECT note FROM events"))
            assert "10016" in notes and "合成小区8" in notes
            assert "SYNTHETIC-WEB-PRIVATE" not in notes
            # Ordinary widget reruns must not automatically retry the failure.
            page.get_by_role("button", name="修改筛选条件", exact=True).click()
            page.get_by_role("button", name="应用筛选", exact=True).click()
            expect(page.get_by_role("button", name="重试失败地址", exact=True)).to_be_visible()
            assert len(api_calls(project)) == 20
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ARTIFACTS / "twenty_processed_one_failure.png"))
            (project / "retry_succeeds").write_text("offline recovery", encoding="utf-8")
            page.get_by_role("button", name="重试失败地址", exact=True).click()
            expect(page.get_by_text("本次定位完成。", exact=True)).to_be_visible(timeout=20000)
            calls = api_calls(project)
            assert len(calls) == 21 and calls[-1]["params"]["address"] == "合成小区8"
            expect(page.get_by_text("定位失败（1）", exact=True)).to_have_count(0)
            assert not page.locator('[data-testid="stException"]').count()
        finally:
            context.close()
            browser.close()
            stop()
            log.close()
