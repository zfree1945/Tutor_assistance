"""QA-12/17: two actual independent browser sessions sharing only a temp DB."""
import csv
import io
from pathlib import Path
import sqlite3

from playwright.sync_api import expect, sync_playwright

from test_history_browser import isolated_server, navigate, download_bytes

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts" / "acceptance" / "verification_20261015" / "browser_sessions"
RAW = "订单号 QA700 小学数学 150/h 线上 周日晚上 每周1次 每次2小时"


def test_qa12_two_browsers_clear_and_filter_without_restoring_deleted_records(isolated_server):
    url,_,project = isolated_server
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            first = browser.new_context(accept_downloads=True).new_page()
            first.goto(url)
            expect(first.get_by_role("heading",name="订单导入",exact=True)).to_be_visible(timeout=20000)
            navigate(first,"模型设置")
            first.get_by_label("API Key",exact=True).fill("SYNTHETIC-TWO-SESSION-KEY")
            first.get_by_label("API Key",exact=True).press("Enter")
            expect(first.get_by_text("当前模型配置已自动保存到本机",exact=False)).to_be_visible()
            navigate(first,"订单导入")
            first.get_by_label("粘贴订单文本",exact=True).fill(RAW)
            first.get_by_role("button",name="应用筛选",exact=True).click()
            expect(first.get_by_role("heading",name="筛选结果",exact=True)).to_be_visible(timeout=20000)
            content = download_bytes(first,"导出当前筛选 CSV",ARTIFACTS/"before_clear.csv")
            assert [r["raw_text"] for r in csv.DictReader(io.StringIO(content.decode("utf-8-sig")))] == [RAW]
            second = browser.new_context().new_page()
            second.goto(url)
            expect(second.get_by_role("heading",name="订单导入",exact=True)).to_be_visible(timeout=20000)
            navigate(second,"开发日志")
            second.get_by_role("button",name="清空全部开发日志",exact=True).click()
            second.get_by_role("button",name="确认清空日志",exact=True).click()
            expect(second.get_by_text("开发日志已清空。当前订单、筛选条件和模型配置保留。",exact=False)).to_be_visible()
            navigate(first,"订单导入")
            first.get_by_role("button",name="应用筛选",exact=True).click()
            navigate(first,"筛选结果")
            expect(first.get_by_text("这条导入日志已被清空",exact=False)).to_be_visible()
            after = download_bytes(first,"导出当前筛选 CSV",ARTIFACTS/"after_clear.csv")
            assert [r["raw_text"] for r in csv.DictReader(io.StringIO(after.decode("utf-8-sig")))] == [RAW]
            with sqlite3.connect(project/".local"/"order_history.sqlite3") as db:
                assert db.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
            first.screenshot(path=str(ARTIFACTS/"stale_session_keeps_orders.png"),full_page=True)
        finally:
            browser.close()
