"""实际浏览器逐条释放 17 条合成订单，验证页面高度与进度元素数量不增长。"""

import importlib.util
from pathlib import Path
import re

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "acceptance" / "parse_progress_20261022"
spec = importlib.util.spec_from_file_location("progress_browser_support", ROOT / "tests" / "regression" / "test_browser.py")
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
isolated_server = support.isolated_server


def test_seventeen_orders_update_one_row_and_bar_without_growing_page(isolated_server):
    url, _, project = isolated_server
    wrapper = project / "wrapper.py"
    original = "pipeline.extract_order = lambda raw, *_: parse_local(raw)"
    stub = r'''import re, time
def gated_extract(raw, *_):
    index = int(re.search(r"PG(\d+)", raw).group(1))
    gate = Path(__file__).parent / f"release_{index}"
    deadline = time.monotonic() + 30
    while not gate.exists():
        if time.monotonic() > deadline:
            raise AssertionError("Browser did not release synthetic order")
        time.sleep(0.05)
    return parse_local(raw)
pipeline.extract_order = gated_extract'''
    source = wrapper.read_text(encoding="utf-8")
    assert original in source
    wrapper.write_text(source.replace(original, stub), encoding="utf-8")
    raw = "\n".join(f"订单号 PG{i:03d} 小学数学 150/h 线上 周六晚上 每周1次" for i in range(17))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.goto(url)
            expect(page.get_by_role("heading", name="订单导入", exact=True)).to_be_visible(timeout=20000)
            page.locator('[data-testid="stSidebar"]').get_by_text("模型设置", exact=True).click()
            page.get_by_label("API Key", exact=True).fill("synthetic-progress-key")
            page.get_by_label("API Key", exact=True).press("Enter")
            page.locator('[data-testid="stSidebar"]').get_by_text("订单导入", exact=True).click()
            page.get_by_label("粘贴订单文本", exact=True).fill(raw)
            page.get_by_label("粘贴订单文本", exact=True).press("Tab")
            page.get_by_role("button", name="应用筛选", exact=True).click()
            line = page.locator('[data-testid="stText"]').filter(has_text=re.compile(r"^正在解析订单："))
            expect(line).to_have_text("正在解析订单：0/17（0%）")
            expect(page.get_by_role("progressbar")).to_have_count(1)
            height = page.locator('[data-testid="stMain"]').evaluate("el => el.scrollHeight")
            for completed in range(1, 17):
                (project / f"release_{completed - 1}").touch()
                expect(line).to_have_count(1)
                expect(line).to_have_text(f"正在解析订单：{completed}/17（{completed / 17:.0%}）")
                expect(page.get_by_role("progressbar")).to_have_count(1)
                expect(page.get_by_role("progressbar")).to_have_attribute("aria-valuenow", str(int(completed / 17 * 100)))
                assert abs(page.locator('[data-testid="stMain"]').evaluate("el => el.scrollHeight") - height) <= 2
                if completed == 5:
                    line.scroll_into_view_if_needed()
                    page.screenshot(path=str(ARTIFACTS / "progress_5_of_17.png"), full_page=True)
            (project / "release_16").touch()
            expect(page.get_by_role("heading", name="筛选结果", exact=True)).to_be_visible(timeout=20000)
            expect(page.get_by_text("解析完成：共 17 条，成功 17 条，失败 0 条", exact=True)).to_have_count(1)
            expect(page.get_by_role("progressbar")).to_have_count(1)
            expect(page.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "100")
            assert not page.get_by_text(re.compile(r"^逐条解析：")).count()
            assert not page.locator('[data-testid="stException"]').count()
            assert not errors
            page.screenshot(path=str(ARTIFACTS / "completed_17.png"))
        finally:
            browser.close()
