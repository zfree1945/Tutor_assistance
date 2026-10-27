"""逐条解析只替换同一个文本和进度条；回调表示该条已经完成。"""

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.delta_generator import DeltaGenerator
from streamlit.testing.v1 import AppTest

from tutor_screen import pipeline
from tutor_screen.llm_client import ModelReplyError
from tutor_screen.local_parser import parse_local


APP = Path(__file__).resolve().parents[2] / "app.py"


def raw_orders(count):
    return "\n".join(f"订单号 PG{i:03d} 小学数学 150/h 线上 周六晚上 每周1次" for i in range(count))


@pytest.mark.parametrize("count", [1, 17])
def test_progress_replaces_single_text_and_bar_and_finishes_after_navigation(count, monkeypatch):
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["api_key"] = "synthetic-progress-key"
    app.session_state["order_text"] = raw_orders(count)
    app.run()
    emitted = []
    original_enqueue = DeltaGenerator._enqueue

    def capture(self, kind, proto, *args, **kwargs):
        result = original_enqueue(self, kind, proto, *args, **kwargs)
        if kind in {"text", "progress", "markdown"}:
            value = proto.value if kind == "progress" else proto.body
            emitted.append((kind, value, result._get_delta_path_str()))
        return result

    monkeypatch.setattr(DeltaGenerator, "_enqueue", capture)
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)):
        app.button(key="apply_filters").click().run()
    assert not app.exception and app.title[0].value == "筛选结果"
    rows = [(value, path) for kind, value, path in emitted if kind == "text" and value.startswith("正在解析订单：")]
    assert [value for value, _ in rows] == [f"正在解析订单：{i}/{count}（{i / count:.0%}）" for i in range(count + 1)]
    assert len({path for _, path in rows}) == 1
    bars = [(value, path) for kind, value, path in emitted if kind == "progress"]
    assert [value for value, _ in bars[:count + 1]] == [0, *(int(i / count * 100) for i in range(1, count + 1))]
    assert len({path for _, path in bars[:count + 2]}) == 1
    assert app.get("progress")[0].value == 100
    assert [item.value for item in app.text if item.value.startswith("解析完成：")] == [f"解析完成：共 {count} 条，成功 {count} 条，失败 0 条"]
    assert not any("逐条解析" in str(value) for _, value, _ in emitted)
    for expected in (f"识别订单边界：0/{count}", f"订单边界识别完成：{count}/{count}", f"执行本地筛选：{count}/{count}"):
        assert any(kind == "markdown" and value == expected for kind, value, _ in emitted)


def test_two_failed_items_are_counted_and_details_start_collapsed():
    app = AppTest.from_file(APP, default_timeout=15)
    app.session_state["api_key"] = "synthetic-progress-key"
    app.session_state["order_text"] = raw_orders(17)
    app.run()
    def local(raw):
        if "PG003" in raw or "PG012" in raw:
            raise ValueError("private-exception")
        return parse_local(raw)

    def model(raw, *_):
        if "PG003" in raw or "PG012" in raw:
            raise ModelReplyError("模型结果无效")
        return parse_local(raw)

    with patch("tutor_screen.pipeline.parse_local", side_effect=local), patch("tutor_screen.pipeline.extract_order", side_effect=model):
        app.button(key="apply_filters").click().run()
    assert not app.exception
    assert any(item.value == "解析完成：共 17 条，成功 15 条，失败 2 条" for item in app.text)
    details = next(item for item in app.expander if item.label == "查看失败详情")
    assert not details.proto.expanded
    assert "ORD-0004" in details.text[0].value and "ORD-0013" in details.text[0].value
    assert "private-exception" not in details.text[0].value
    assert len(app.session_state["orders"]) == 17
    assert [order.raw_text for order in app.session_state["orders"]] == raw_orders(17).splitlines()


def test_zero_orders_shows_message_without_division_or_model_call():
    app = AppTest.from_file(APP)
    app.session_state["api_key"] = "synthetic-progress-key"
    app.session_state["order_text"] = "本批订单"
    app.run()
    with patch("tutor_screen.pipeline.split_orders", return_value=[]), patch("tutor_screen.pipeline.extract_order") as model:
        app.button(key="apply_filters").click().run()
        model.assert_not_called()
    assert not app.exception
    assert [item.value for item in app.text] == ["没有可解析的订单"]
    assert app.get("progress")[0].value == 0


def test_completion_callback_observes_finished_items_and_preserves_results():
    finished = []
    notifications = []
    def extract(raw, *_):
        value = parse_local(raw)
        finished.append(raw)
        return value

    def progress(stage, current, total):
        if stage == "逐条解析":
            assert len(finished) == current
            notifications.append((current, total))

    raw = raw_orders(17)
    with patch("tutor_screen.pipeline.extract_order", side_effect=extract):
        actual = pipeline.process_batch(raw, "synthetic", "https://example.test", "model", progress)
    with patch("tutor_screen.pipeline.extract_order", side_effect=lambda text, *_: parse_local(text)):
        expected = pipeline.process_batch(raw, "synthetic", "https://example.test", "model")
    assert actual == expected
    assert notifications == [(i, 17) for i in range(1, 18)]
