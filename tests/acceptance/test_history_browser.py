"""真实浏览器验收日志：使用隔离副本，重启测试服务，不接触用户数据。"""
import importlib.util
import json
from pathlib import Path
import re

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "acceptance" / "history"
spec = importlib.util.spec_from_file_location(
    "history_browser_support", ROOT / "tests" / "regression" / "test_browser.py"
)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
isolated_server = support.isolated_server

RAW = (
    "S218080【小学全科，鹿城八仙楼小区】新6年级女，作业全科错题答疑。"
    "数学加强难题训练（附加题）。，一周一次课，一次小时，时间协调，"
    "大学生150两小时，要求在读大学生,在读硕士生，要求师范生，擅长数学科目。"
)
CONTENT = (RAW + "\r\n").encode("utf-8-sig")
KEY = "dummy-history-browser-key"


def navigate(page, name):
    page.locator('[data-testid="stSidebar"]').get_by_text(name, exact=True).click()
    expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()


def download_bytes(page, label, target):
    with page.expect_download() as download:
        page.get_by_role("button", name=label, exact=True).click()
    download.value.save_as(target)
    return target.read_bytes()


def expect_count(page, count):
    expect(page.locator('[data-testid="stMetricValue"]').first).to_have_text(str(count))


def test_upload_parse_debug_download_restart_and_manual_clear(isolated_server):
    url, restart, project = isolated_server
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible(timeout=20000)
        page.locator('input[type="file"]').set_input_files(
            {"name": "温州测试订单.txt", "mimeType": "text/plain", "buffer": CONTENT}
        )
        expect(page.get_by_text("已暂存文件：温州测试订单.txt", exact=False)).to_be_visible()
        # 选择文件立即留档，无需配置模型或启动解析。
        navigate(page, "开发日志")
        expect_count(page, 1)
        expect(page.get_by_text("上传记录已保存，尚未进行解析。", exact=True)).to_be_visible()
        page.get_by_text("本次导入原文", exact=True).click()
        expect(page.get_by_text(RAW, exact=False)).to_be_visible()
        assert download_bytes(page, "下载原始TXT", ARTIFACTS / "original_upload.txt") == CONTENT

        navigate(page, "模型设置")
        expect(page.get_by_text("填写 API Key 后自动保存", exact=False)).to_be_visible()
        page.get_by_label("API Key", exact=True).fill(KEY)
        page.get_by_label("API Key", exact=True).press("Enter")
        expect(page.get_by_text("当前模型配置已自动保存到本机", exact=False)).to_be_visible()
        navigate(page, "订单导入")
        expect(page.get_by_text("已暂存文件：温州测试订单.txt", exact=False)).to_be_visible()
        page.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible(timeout=20000)
        page.get_by_role("tab", name=re.compile("可能匹配")).click()
        raw_details = page.locator('[data-testid="stExpander"] details').filter(has=page.get_by_text("查看原始订单", exact=True))
        if raw_details.get_attribute("open") is None:
            page.get_by_text("查看原始订单", exact=True).click()
        expect(page.get_by_text(RAW, exact=True)).to_be_visible()
        expect(page.get_by_text(re.compile("待确认条件：.*每次时长"))).to_be_visible()
        for name in ["结构化字段", "字段来源", "本地匹配理由", "提取方式"]:
            assert page.get_by_text(name, exact=False).count() == 0
        page.get_by_text(RAW, exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS / "result_card.png"), full_page=True)

        navigate(page, "开发日志")
        expect_count(page, 1)
        expect(page.get_by_text("结构化字段", exact=True)).to_be_visible()
        expect(page.get_by_text("字段来源", exact=True)).to_be_visible()
        expect(page.get_by_text("本地匹配理由：", exact=False)).to_be_visible()
        expect(page.get_by_text("提取方式：", exact=False)).to_be_visible()
        exported = download_bytes(page, "下载此记录JSON", ARTIFACTS / "import_log.json")
        record = json.loads(exported)
        assert KEY not in exported.decode("utf-8")
        assert record["input_text"] == RAW + "\r\n"
        assert record["filename"] == "温州测试订单.txt"
        assert [event["kind"] for event in record["events"]] == ["开始解析", "解析完成"]
        order = record["events"][-1]["orders"][0]
        assert order["raw_text"] == RAW and order["field_sources"]
        assert order["match_reason"] and order["extraction_source"] == "model"
        page.get_by_text("结构化字段", exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS / "development_log.png"), full_page=True)
        assert not page.locator('[data-testid="stException"]').count() and errors == []
        context.close()

        # 真正重启进程并创建新浏览器会话，日志独立于会话状态。
        restart()
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page = context.new_page()
        page.goto(url)
        expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible(timeout=20000)
        expect(page.get_by_label("粘贴订单文本", exact=True)).to_have_value(RAW + "\n")
        navigate(page, "开发日志")
        expect_count(page, 1)
        assert download_bytes(page, "下载原始TXT", ARTIFACTS / "after_restart.txt") == CONTENT
        expect(page.get_by_text("字段来源", exact=True)).to_be_visible()
        assert KEY.encode() not in (project / ".local" / "order_history.sqlite3").read_bytes()

        page.get_by_role("button", name="清空全部开发日志", exact=True).click()
        page.get_by_role("button", name="取消清空日志", exact=True).click()
        expect_count(page, 1)
        page.get_by_role("button", name="清空全部开发日志", exact=True).click()
        page.get_by_role("button", name="确认清空日志", exact=True).click()
        expect_count(page, 0)
        expect(page.get_by_text("开发日志已清空。", exact=False)).to_be_visible()
        navigate(page, "模型设置")
        expect(page.get_by_label("API Key", exact=True)).to_have_value(KEY)
        context.close()
        restart()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.goto(url)
        expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible(timeout=20000)
        expect(page.get_by_label("粘贴订单文本", exact=True)).to_have_value(RAW + "\n")
        navigate(page, "开发日志")
        expect_count(page, 0)
        assert RAW.encode() not in (project / ".local" / "order_history.sqlite3").read_bytes()
        assert not page.locator('[data-testid="stException"]').count()
        context.close()
        browser.close()


def test_invalid_encoding_upload_is_saved_and_downloadable(isolated_server):
    url, _, _ = isolated_server
    content = b"\xff\xfe\x00\x81 invalid UTF-8"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        page.goto(url)
        expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible(timeout=20000)
        try:
            page.locator('input[type="file"]').set_input_files(
                {"name": "encoding_error.txt", "mimeType": "text/plain", "buffer": content}
            )
        except Exception:
            page.screenshot(path=str(ARTIFACTS / "encoding_upload_failure.png"), full_page=True)
            (ARTIFACTS / "encoding_upload_failure.txt").write_text(page.locator("body").inner_text(), encoding="utf-8")
            raise
        expect(page.get_by_text("已暂存文件：encoding_error.txt", exact=False)).to_be_visible()
        page.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.get_by_text("TXT 文件需为 UTF-8 编码。", exact=True)).to_be_visible()
        navigate(page, "开发日志")
        expect_count(page, 1)
        expect(page.get_by_text("TXT需为UTF-8编码，原始文件已保留供检查。", exact=True)).to_be_visible()
        assert download_bytes(page, "下载原始TXT", ARTIFACTS / "invalid_encoding.txt") == content
        assert not page.locator('[data-testid="stException"]').count()
        browser.close()
