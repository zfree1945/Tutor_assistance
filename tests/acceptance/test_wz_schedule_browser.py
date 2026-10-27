"""四条用户样例的实际卡片、日志下载和六次频率筛选。"""
import json
from pathlib import Path
import re

import pytest
pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright

from test_history_browser import isolated_server, navigate, download_bytes
from wz_schedule_samples import RAW

ARTIFACTS = Path(__file__).parents[2] / "artifacts" / "acceptance" / "wz_schedules"


def test_four_user_orders_precise_time_cards_and_log_frequency_filter(isolated_server):
    url, _, _ = isolated_server
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible(timeout=20000)
        navigate(page, "模型设置")
        expect(page.get_by_text("填写 API Key 后自动保存", exact=False)).to_be_visible()
        page.get_by_label("API Key", exact=True).fill("dummy-wz-browser-key")
        page.get_by_label("API Key", exact=True).press("Enter")
        expect(page.get_by_text("当前模型配置已自动保存到本机", exact=False)).to_be_visible()
        navigate(page, "订单导入")
        page.get_by_label("粘贴订单文本", exact=True).fill(RAW)
        page.get_by_label("粘贴订单文本", exact=True).press("Tab")
        page.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible(timeout=20000)
        expect(page.get_by_text("上课时间：周日 13:00–15:00", exact=True)).to_be_visible()
        page.get_by_text("上课时间：周日 13:00–15:00", exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS / "simple_clock_card.png"), full_page=True)
        page.get_by_role("tab", name=re.compile("可能匹配")).click()
        expect(page.get_by_text("上课时间：周二、周三、周四、周五 14:00–20:30", exact=True)).to_be_visible()
        expect(page.get_by_text("上课时间：周六、周日 09:00–17:00", exact=True)).to_be_visible()
        pending = page.get_by_text(re.compile("待确认条件：.*时薪"))
        assert "每次时长" in pending.inner_text() and "每周次数" not in pending.inner_text()
        page.get_by_text("上课时间：周六、周日 09:00–17:00", exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS / "grouped_clock_card.png"), full_page=True)
        navigate(page, "开发日志")
        record = json.loads(download_bytes(page, "下载此记录JSON", ARTIFACTS / "browser_log.json"))
        assert record["input_text"] == RAW
        assert [item["weekly_sessions"] for item in record["events"][-1]["orders"]] == [1, 4, 5, 6]
        assert len(record["events"][-1]["orders"][3]["schedule_windows"]) == 2
        navigate(page, "订单导入")
        expect(page.locator('[data-testid="stApp"]')).to_have_attribute("data-test-script-state", "notRunning")
        frequency = page.get_by_label("每周最多次数", exact=True)
        frequency.click()
        # 返回页面后点击可编辑输入框可能仅获得焦点，用键盘明确展开选项。
        frequency.press("ArrowDown")
        expect(page.get_by_role("listbox")).to_be_visible()
        page.get_by_role("option", name="5", exact=True).click()
        expect(frequency).to_have_value("5")
        page.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible()
        expect(page.get_by_text("当前保留 3 条 · 已排除 1 条", exact=False)).to_be_visible()
        navigate(page, "开发日志")
        record = json.loads(download_bytes(page, "下载此记录JSON", ARTIFACTS / "browser_filtered_log.json"))
        assert record["events"][-1]["orders"][3]["match_status"] == "已排除"
        assert "次数" in record["events"][-1]["orders"][3]["match_reason"]
        assert not page.locator('[data-testid="stException"]').count() and not errors
        browser.close()
