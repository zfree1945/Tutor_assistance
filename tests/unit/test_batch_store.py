"""当前批次的原子保存、旧日志迁移和跨进程恢复。"""

from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from tutor_screen import batch_store as store, config_store, history_store
from tutor_screen.local_parser import parse_local
from tutor_screen.models import Criteria, Order


RAW = '批次标题\n订单号 LOCAL101 小学数学 100/h 线上 周六晚上\n中文,"引号"\n'


def saved_batch():
    order = Order(**parse_local(RAW).model_dump(), order_id="LOCAL101", raw_text=RAW)
    return store.SavedBatch(input_text=RAW, orders=[order], criteria=Criteria(min_hourly_rate=150))


def test_batch_roundtrip_and_restore_in_another_process():
    batch = saved_batch()
    store.save_batch(batch)
    assert store.load_batch() == batch
    script = '''import sys
from pathlib import Path
from tutor_screen import config_store, batch_store
config_store.CONFIG_PATH = Path(sys.argv[1])
print(batch_store.load_batch().model_dump_json())
'''
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(config_store.CONFIG_PATH)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, encoding="utf-8", check=True)
    assert store.SavedBatch.model_validate_json(result.stdout) == batch


def test_no_previous_successful_batch_returns_none():
    history_store.create_import(source_type="粘贴", input_text="未解析草稿")
    assert store.load_batch() is None
    assert not store.batch_path().exists()


def test_old_logs_restore_latest_parsed_snapshot_including_excluded_orders():
    batch = saved_batch()
    previous = history_store.create_import(source_type="粘贴", input_text="较早批次")
    history_store.append_event(previous, kind="解析完成", parsed_orders=batch.orders)
    current = history_store.create_import(source_type="TXT", input_text=RAW, filename="synthetic.txt")
    history_store.append_event(current, kind="解析完成", parsed_orders=batch.orders)
    history_store.append_event(current, kind="应用筛选", criteria=batch.criteria, parsed_orders=batch.orders)
    failed = history_store.create_import(source_type="粘贴", input_text="较新但失败")
    history_store.append_event(failed, kind="解析未完成", status="未完成")
    restored = store.load_batch()
    assert restored.input_text == RAW and restored.import_id == current
    assert restored.orders == batch.orders and restored.criteria == batch.criteria
    assert history_store.has_import(current) and not history_store.has_import("missing")


def test_failed_replace_preserves_previous_snapshot_and_removes_temporary_file():
    store.save_batch(saved_batch())
    original = store.batch_path().read_bytes()
    with patch("tutor_screen.batch_store.os.replace", side_effect=OSError("PRIVATE-EXCEPTION")):
        with pytest.raises(store.BatchStoreError) as error:
            store.save_batch(saved_batch().model_copy(update={"input_text": "替换内容"}))
    assert "PRIVATE-EXCEPTION" not in str(error.value)
    assert store.batch_path().read_bytes() == original
    assert not list(store.batch_path().parent.glob(".batch-*.tmp"))


@pytest.mark.parametrize("content", [b"invalid PRIVATE", b'{"version":2}', b'{"input_text":"x","orders":[]}',
                                     b'{"input_text":"x","orders":[{"order_id":"broken"}]}'])
def test_corrupt_or_unsupported_batch_is_reported_without_overwriting(content):
    path = store.batch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with pytest.raises(store.BatchStoreError) as error:
        store.load_batch()
    assert "PRIVATE" not in str(error.value)
    assert path.read_bytes() == content


def test_batch_is_independent_of_history_clearing():
    store.save_batch(saved_batch())
    history_store.clear_history()
    assert store.load_batch() == saved_batch()


def test_unwritable_parent_keeps_existing_file(monkeypatch, tmp_path):
    parent = tmp_path / "file"
    parent.write_bytes(b"original")
    monkeypatch.setattr(config_store, "CONFIG_PATH", parent / "config.dpapi")
    with pytest.raises(store.BatchStoreError):
        store.save_batch(saved_batch())
    assert parent.read_bytes() == b"original"
