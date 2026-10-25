"""PERF：仅离线性能；不能用来推断在线模型速度。"""
import json
from pathlib import Path
import time
import tracemalloc
from unittest.mock import patch

from tutor_screen.local_parser import parse_local
from tutor_screen.matching import apply_criteria
from tutor_screen.models import Criteria
from tutor_screen.pipeline import process_batch


def test_300_orders_three_runs_and_repeated_filtering():
    text = "\n".join(f"订单号 A{i:03d} 小学数学 {100+i}/h 线上 周六晚上 每周1次" for i in range(300))
    measurements = []
    tracemalloc.start()
    try:
        with patch("tutor_screen.pipeline.extract_order", side_effect=lambda raw, *_: parse_local(raw)) as extract:
            for _ in range(3):
                begin = time.perf_counter()
                orders = process_batch(text, "key", "https://example.test", "model")
                elapsed = time.perf_counter() - begin
                assert len(orders) == 300 and len({v.order_id for v in orders}) == 300
                assert "\n".join(v.raw_text for v in orders) == text
                measurements.append({"batch_seconds": round(elapsed, 4), "peak_python_mb": round(tracemalloc.get_traced_memory()[1] / 1024**2, 3)})
            extract.reset_mock()
            begin = time.perf_counter()
            for _ in range(10):
                filtered = apply_criteria(orders, Criteria(min_hourly_rate=150))
                assert len(filtered) == 250
            filtering = (time.perf_counter() - begin) / 10
            extract.assert_not_called()
    finally:
        tracemalloc.stop()
    output = Path(__file__).resolve().parents[2] / "artifacts" / "regression"
    output.mkdir(parents=True, exist_ok=True)
    (output / "performance.json").write_text(json.dumps({"rounds": measurements, "filter_mean_seconds": round(filtering, 4), "filter_model_calls": 0, "memory_scope": "tracemalloc Python allocations; not process RSS"}, indent=2), encoding="utf-8")
