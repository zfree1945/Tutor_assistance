from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen.local_parser import parse_local
from tutor_screen.pipeline import process_batch
from tutor_screen.segmentation import Segment, find_order_markers


APP = Path(__file__).resolve().parents[2] / "app.py"
SAMPLE = (Path(__file__).parent / "fixtures/sixteen_orders_with_decoration.txt").read_text(encoding="utf-8")
NUMBERS = {
    "温州26102008", "温州26101953", "温州29101232", "温州线下ZYM26101902",
    "温州线下26102003", "wz2391", "wz2390", "wz2386", "wz2383", "wz2381",
    "wz2379", "wz2377", "wz2359", "wz2350", "温州102064", "温州线上102111",
}


def test_complete_sample_has_sixteen_orders_and_reports_one_ignored_fragment():
    progress = []
    assert {SAMPLE[m.start:m.end] for m in find_order_markers(SAMPLE)} == NUMBERS
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)) as model:
        orders = process_batch(SAMPLE, "synthetic", "https://example.test", "model",
                               lambda *args: progress.append(args))
    assert len(orders) == model.call_count == 16
    assert {order.order_number for order in orders} == NUMBERS
    assert [order.order_id for order in orders] == [f"ORD-{i:04d}" for i in range(1, 17)]
    assert ("已忽略非订单内容", 1, 0) in progress
    assert ("订单边界识别完成", 16, 16) in progress


@pytest.mark.parametrize("raw", [
    "地址★示例小区\n学员★初二\n科目★数学",
    "科目★",  # 有标签即保留，即使正文很短。
    "订单号 AB123",  # 有编号即保留。
    "小学数学学生需要辅导",  # 无标签但有足够正文。
    "一二三四五六七八",  # 八字边界保留。
])
def test_real_or_reviewable_content_is_kept_and_ignored_fragments_do_not_use_ids(raw):
    events = []
    with patch("tutor_screen.pipeline.split_orders", return_value=[Segment("☄..."), Segment(raw), Segment("谢谢合作！")]), \
            patch("tutor_screen.pipeline.extract_order", side_effect=lambda text, *_: parse_local(text)):
        orders = process_batch("input", "synthetic", "https://example.test", "model", lambda *e: events.append(e))
    assert len(orders) == 1 and orders[0].raw_text == raw
    assert orders[0].order_id == "ORD-0001"
    assert ("已忽略非订单内容", 2, 0) in events


def test_summary_keeps_ignored_count_after_navigation():
    app = AppTest.from_file(APP, default_timeout=20)
    app.session_state["api_key"] = "synthetic"
    app.session_state["order_text"] = SAMPLE
    app.run()
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        app.button(key="apply_filters").click().run()
    assert not app.exception
    assert len(app.session_state["orders"]) == 16
    assert any(item.value == "解析完成：共 16 条，成功 16 条，失败 0 条；已忽略非订单内容 1 段" for item in app.text)


def test_only_decoration_is_reported_without_model_requests():
    app = AppTest.from_file(APP, default_timeout=20)
    app.session_state["api_key"] = "synthetic"
    app.session_state["order_text"] = "-. * ☄. * *. 🌙*.. *.."
    app.run()
    with patch("tutor_screen.pipeline.extract_order") as extract, patch("tutor_screen.pipeline.try_split") as split:
        app.button(key="apply_filters").click().run()
    assert not app.exception
    assert any(item.value == "已忽略非订单内容 1 段" for item in app.info)
    extract.assert_not_called()
    split.assert_not_called()
