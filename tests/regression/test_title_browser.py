"""用户五条订单的真实浏览器验收；复用临时副本和模拟模型。"""
import csv
import io
from pathlib import Path

import pytest
pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright
from test_browser import isolated_server
from title_order_samples import BATCH, EXPECTED, RAWS

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts" / "regression" / "title_orders"


def test_user_order_addresses_exclusions_and_download_in_browser(isolated_server):
    url, _, _ = isolated_server
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible(timeout=20000)
        page.locator('[data-testid="stSidebar"]').get_by_text("模型设置", exact=True).click()
        expect(page.get_by_text("填写 API Key 后自动保存", exact=False)).to_be_visible()
        page.get_by_label("API Key", exact=True).fill("dummy-title-browser-key")
        page.get_by_label("API Key", exact=True).press("Enter")
        expect(page.get_by_text("当前模型配置已自动保存", exact=False)).to_be_visible()
        page.get_by_text("订单导入", exact=True).click()
        page.get_by_label("粘贴订单文本", exact=True).fill(BATCH)
        page.get_by_label("粘贴订单文本", exact=True).press("Tab")
        page.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible(timeout=20000)
        expect(page.get_by_role("tab", name="可能匹配（5）", exact=True)).to_be_visible()
        expect(page.get_by_role("tab", name="无法解析（0）", exact=True)).to_be_visible()
        possible_tab = page.get_by_role("tab", name="可能匹配（5）", exact=True)
        expect(possible_tab).to_have_count(1)
        expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
        possible_tab.click()
        expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
        expect(page.locator('[role="tabpanel"]:visible [class*="st-key-order_card_"]')).to_have_count(5)
        for item in EXPECTED:
            address = page.get_by_text("地址：" + item["address"], exact=False)
            expect(address).to_have_count(1)
            expect(address).to_be_visible()
        expect(page.get_by_text("排除时段：周六除了下午1-3点", exact=False)).to_be_visible()
        page.screenshot(path=str(ARTIFACTS / "user_orders_desktop.png"), full_page=True)
        with page.expect_download() as download:
            page.get_by_role("button", name="导出当前筛选 CSV", exact=True).click()
        download.value.save_as(ARTIFACTS / "user_orders.csv")
        decoded = (ARTIFACTS / "user_orders.csv").read_text(encoding="utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(decoded)))
        assert len(rows) == 5
        assert [row["address"] for row in rows] == [item["address"] for item in EXPECTED]
        assert [row["raw_text"] for row in rows] == RAWS
        assert "1-3点" in rows[0]["excluded_times"]
        assert rows[0]["order_number"] == "S188519-2"
        assert rows[1]["lesson_hours"] == "2.5"
        assert "dummy-title-browser-key" not in decoded
        assert not page.locator('[data-testid="stException"]').count()
        assert errors == []
        context.close()
        browser.close()
