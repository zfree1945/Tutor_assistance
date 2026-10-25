"""实际Chromium验收：临时测试副本、独立端口、模拟模型，测试后终止专用进程。"""
import csv
import io
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright, expect, TimeoutError as BrowserTimeout

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "regression"
RAW = "订单号 A100 小学数学 150/h 线上 周六晚上 每周1次\n订单号 A101 初中英语 100/h 线上 周日上午 每周1次"


@pytest.fixture
def isolated_server(tmp_path):
    project = tmp_path / "browser_project"
    project.mkdir()
    shutil.copy2(ROOT / "app.py", project / "app.py")
    shutil.copytree(ROOT / "tutor_screen", project / "tutor_screen", ignore=shutil.ignore_patterns("__pycache__"))
    (project / "wrapper.py").write_text('''from pathlib import Path
import runpy
import sys
sys.path.insert(0, str(Path(__file__).parent))
from tutor_screen import pipeline, llm_client
from tutor_screen.local_parser import parse_local
pipeline.extract_order = lambda raw, *_: parse_local(raw)
def forbidden(*args, **kwargs):
    raise AssertionError("No external model requests in browser acceptance")
llm_client._client = forbidden
runpy.run_path(str(Path(__file__).parent / "app.py"), run_name="__main__")
''', encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = None
    log = (tmp_path / "server.log").open("wb")

    def stop():
        nonlocal process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def start():
        nonlocal process
        stop()
        process = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "wrapper.py", "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true", "--browser.gatherUsageStats=false"], cwd=project, stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Isolated Streamlit process exited before becoming ready")
            try:
                with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as reply:
                    if reply.status == 200:
                        return f"http://127.0.0.1:{port}"
            except OSError:
                pass
            time.sleep(0.2)
        pytest.fail("Isolated Streamlit did not become ready")

    try:
        yield start(), start, project
    finally:
        stop()
        log.close()


def test_real_upload_card_click_download_mobile_and_restart(isolated_server):
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
        for key in ["step_settings", "step_import", "step_results", "clear_orders", "start_processing"]:
            expect(page.locator(f".st-key-{key}")).to_have_count(0)
        page.locator('[data-testid="stSidebar"]').get_by_text("模型设置", exact=True).click()
        expect(page.get_by_role("heading", name="模型设置", exact=True)).to_be_visible()
        expect(page.get_by_text("填写 API Key 后自动保存", exact=False)).to_be_visible()
        page.get_by_label("API Key", exact=True).fill("dummy-browser-key")
        page.get_by_label("API Key", exact=True).press("Enter")
        expect(page.get_by_text("当前模型配置已自动保存到本机", exact=False)).to_be_visible()
        page.get_by_text("订单导入", exact=True).click()
        page.locator('input[type="file"]').set_input_files({"name": "browser_orders.txt", "mimeType": "text/plain", "buffer": RAW.encode("utf-8-sig")})
        expect(page.get_by_text("已暂存文件：browser_orders.txt", exact=False)).to_be_visible()
        page.locator('[data-testid="stSidebar"]').get_by_text("筛选结果", exact=True).click()
        expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible()
        page.get_by_text("订单导入", exact=True).click()
        expect(page.get_by_text("已暂存文件：browser_orders.txt", exact=False)).to_be_visible()
        page.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible(timeout=20000)
        with page.expect_download() as download:
            page.get_by_role("button", name="导出当前筛选 CSV", exact=True).click()
        first_download = download.value
        first_download.save_as(ARTIFACTS / "browser_all.csv")
        rows = list(csv.DictReader(io.StringIO((ARTIFACTS / "browser_all.csv").read_text(encoding="utf-8-sig"))))
        assert len(rows) == 2 and [row["raw_text"] for row in rows] == RAW.splitlines()
        assert "dummy-browser-key" not in (ARTIFACTS / "browser_all.csv").read_text(encoding="utf-8-sig")
        page.get_by_text("订单导入", exact=True).click()
        page.get_by_label("最低时薪（元/小时）", exact=True).fill("150")
        page.get_by_label("最低时薪（元/小时）", exact=True).press("Tab")
        page.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible()
        expect(page.get_by_text("当前保留 1 条 · 已排除 1 条", exact=False)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("button", name="导出明确匹配 CSV", exact=True).click()
        download.value.save_as(ARTIFACTS / "browser_matched.csv")
        assert len(list(csv.DictReader((ARTIFACTS / "browser_matched.csv").open(encoding="utf-8-sig", newline="")))) == 1
        page.screenshot(path=str(ARTIFACTS / "browser_desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        # 小屏幕的展开侧栏是浮层，先用真实按钮收起，再验收主内容。
        sidebar_button = page.locator('[data-testid="stSidebar"] button').first
        box = sidebar_button.bounding_box()
        if box and 0 <= box["x"] < 390:
            try:
                sidebar_button.click(timeout=2000)
            except BrowserTimeout:
                # 响应式动画可能已经自动收起；此时按钮会移到视口外。
                box = sidebar_button.bounding_box()
                assert box is None or box["x"] < 0
        expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible()
        # 允许1px的浏览器舍入误差，验证页面没有横向溢出。
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        # 结果页新增顶部地图后会超过一屏；先回到页首再检查固定工具栏。
        page.locator('[data-testid="stMain"]').evaluate("el => el.scrollTop = 0")
        heading_box = page.get_by_role("heading", name="筛选结果", exact=True).bounding_box()
        toolbar_box = page.get_by_role("banner").bounding_box()
        assert heading_box["y"] >= toolbar_box["y"] + toolbar_box["height"], "窄屏标题被固定工具栏遮挡"
        page.screenshot(path=str(ARTIFACTS / "browser_mobile.png"), full_page=True)
        page.get_by_role("button", name="导出当前筛选 CSV", exact=True).scroll_into_view_if_needed()
        expect(page.get_by_role("button", name="导出明确匹配 CSV", exact=True)).to_be_visible()
        page.screenshot(path=str(ARTIFACTS / "browser_mobile_results.png"), full_page=True)
        assert not page.locator('[data-testid="stException"]').count()
        assert errors == []
        context.close()
        assert b"dummy-browser-key" not in (project / ".local" / "model_config.dpapi").read_bytes()
        # 真正重启隔离服务进程，再刷新页面；原文、解析订单和已应用条件均恢复。
        restart()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.goto(url)
        expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible(timeout=20000)
        expect(page.get_by_label("粘贴订单文本", exact=True)).to_have_value(RAW)
        page.reload()
        expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible(timeout=20000)
        expect(page.get_by_label("粘贴订单文本", exact=True)).to_have_value(RAW)
        expect(page.get_by_label("最低时薪（元/小时）", exact=True)).to_have_value("150.00")
        page.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible()
        expect(page.get_by_text("当前保留 1 条 · 已排除 1 条", exact=False)).to_be_visible()
        page.locator('[data-testid="stSidebar"]').get_by_text("模型设置", exact=True).click()
        expect(page.get_by_label("API Key", exact=True)).to_have_value("dummy-browser-key")
        context.close()
        browser.close()
