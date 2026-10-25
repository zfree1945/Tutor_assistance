"""日志事务、完整内容、跨进程恢复和手动清空。"""
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tutor_screen import config_store, history_store as store
from tutor_screen.config_store import ModelConfig, save_config
from tutor_screen.local_parser import parse_local
from tutor_screen.matching import evaluate_order
from tutor_screen.models import Criteria, Order
from tutor_screen.presentation import confirmation_labels

ROOT = Path(__file__).parents[2]
RAW = '订单号 A100 小学数学 150/h 线上 周六晚上 每周1次，含中文,"引号"'


def make_order(raw=RAW):
    return Order(**parse_local(raw).model_dump(), order_id="ORD-0001", raw_text=raw, extraction_source="model")


def test_uploaded_bytes_and_text_roundtrip_before_any_parsing():
    content = ("\ufeff" + RAW + "\r\n").encode("utf-8")
    record_id = store.create_import(source_type="TXT", filename="订单'测试.txt", input_text=RAW + "\r\n", file_content=content)
    record = store.get_import(record_id)
    assert record["input_text"] == RAW + "\r\n" and record["file_content"] == content
    assert record["file_size"] == len(content) and record["events"] == []
    assert record["created_at"].endswith("+08:00") and record["status"] == "未解析"


def test_identical_imports_and_repeated_order_ids_are_independent():
    ids = [store.create_import(source_type="粘贴", input_text=RAW) for _ in range(2)]
    assert len(set(ids)) == 2
    for value in ids:
        store.append_event(value, kind="解析完成", orders=[evaluate_order(make_order(), Criteria())])
    assert store.count_imports() == 2
    assert all(store.get_import(value)["events"][0]["orders"][0]["order_id"] == "ORD-0001" for value in ids)


def test_parsed_and_evaluated_snapshots_keep_fields_sources_and_exclusion_reason():
    record_id = store.create_import(source_type="粘贴", input_text=RAW)
    parsed = make_order()
    criteria = Criteria(min_hourly_rate=200)
    evaluated = evaluate_order(parsed, criteria)
    store.append_event(record_id, kind="应用筛选", criteria=criteria, orders=[evaluated], parsed_orders=[parsed])
    event = store.get_import(record_id)["events"][0]
    assert event["criteria"] == criteria.model_dump()
    assert event["orders"][0]["match_status"] == "已排除" and "低于" in event["orders"][0]["match_reason"]
    assert event["orders"][0]["field_sources"] == parsed.field_sources
    assert event["orders"][0]["extraction_source"] == "model"
    assert event["parsed_orders"][0] == parsed.model_dump()
    assert parsed.match_status == "可能匹配"


def test_history_is_restored_in_another_python_process():
    record_id = store.create_import(source_type="粘贴", input_text=RAW)
    store.append_event(record_id, kind="解析完成", orders=[make_order()])
    script = """from pathlib import Path
import sys, json
from tutor_screen import history_store
history_store.HISTORY_PATH = Path(sys.argv[1])
record = history_store.get_import(sys.argv[2])
print(json.dumps({'raw': record['input_text'], 'count': len(record['events'])}, ensure_ascii=False))
"""
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(store.HISTORY_PATH), record_id], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout) == {"raw": RAW, "count": 1}


def test_event_and_import_status_update_are_atomic_and_error_is_safe():
    record_id = store.create_import(source_type="粘贴", input_text=RAW)
    with sqlite3.connect(store.HISTORY_PATH) as connection:
        connection.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON imports BEGIN SELECT RAISE(ABORT, 'dummy-secret-key'); END")
    with pytest.raises(store.HistoryStoreError) as error:
        store.append_event(record_id, kind="解析完成", orders=[make_order()])
    assert "dummy-secret-key" not in str(error.value)
    record = store.get_import(record_id)
    assert record["status"] == "未解析" and record["events"] == []


def test_corrupt_database_reports_error_and_preserves_original():
    original = b"invalid sqlite with dummy-secret-key"
    store.HISTORY_PATH.write_bytes(original)
    with pytest.raises(store.HistoryStoreError) as error:
        store.count_imports()
    assert "dummy-secret-key" not in str(error.value)
    assert store.HISTORY_PATH.read_bytes() == original


def test_valid_json_with_invalid_order_schema_is_reported_as_corrupt_log():
    record_id = store.create_import(source_type="粘贴", input_text=RAW)
    store.append_event(record_id, kind="解析完成", orders=[make_order()])
    with sqlite3.connect(store.HISTORY_PATH) as connection:
        connection.execute("UPDATE events SET orders_json=?", ('[{"order_id":"broken"}]',))
    with pytest.raises(store.HistoryStoreError):
        store.get_import(record_id)


def test_failed_clear_rolls_back_all_records():
    record_id = store.create_import(source_type="粘贴", input_text=RAW)
    store.append_event(record_id, kind="解析完成", orders=[make_order()])
    with sqlite3.connect(store.HISTORY_PATH) as connection:
        connection.execute("CREATE TRIGGER block_clear BEFORE DELETE ON imports BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    with pytest.raises(store.HistoryStoreError):
        store.clear_history()
    assert store.count_imports() == 1 and len(store.get_import(record_id)["events"]) == 1


def test_post_delete_vacuum_failure_does_not_report_clear_as_failed(monkeypatch):
    store.create_import(source_type="粘贴", input_text=RAW)
    original = store._database
    class Proxy:
        def __init__(self, connection): self.connection = connection
        def __enter__(self): return self.connection.__enter__()
        def __exit__(self, *args): return self.connection.__exit__(*args)
        def execute(self, sql, *args):
            if sql == "VACUUM": raise sqlite3.OperationalError("locked")
            return self.connection.execute(sql, *args)
    @contextmanager
    def fail_vacuum():
        with original() as connection:
            yield Proxy(connection)
    with monkeypatch.context() as context:
        context.setattr(store, "_database", fail_vacuum)
        store.clear_history()
    assert store.count_imports() == 0


def test_unwritable_parent_reports_error(monkeypatch, tmp_path):
    parent = tmp_path / "parent_is_a_file"
    parent.write_bytes(b"original")
    monkeypatch.setattr(store, "HISTORY_PATH", parent / "logs.sqlite3")
    with pytest.raises(store.HistoryStoreError):
        store.create_import(source_type="粘贴", input_text=RAW)
    assert parent.read_bytes() == b"original"


def test_manual_clear_removes_all_log_content_and_preserves_model_config():
    save_config(ModelConfig(provider="OpenAI", api_key="dummy-kept-config", base_url="https://example.test", model_name="model"))
    before = config_store.CONFIG_PATH.read_bytes()
    marker = "RAW-ORDER-TO-BE-DELETED"
    record_id = store.create_import(source_type="TXT", input_text=marker, filename="test.txt", file_content=marker.encode())
    store.append_event(record_id, kind="解析完成", orders=[make_order()])
    store.clear_history()
    assert store.count_imports() == 0 and store.get_import(record_id) is None
    assert marker.encode() not in store.HISTORY_PATH.read_bytes()
    assert config_store.CONFIG_PATH.read_bytes() == before
    with pytest.raises(store.HistoryStoreError):
        store.append_event(record_id, kind="应用筛选", orders=[make_order()])
    assert store.count_imports() == 0


def test_pagination_keeps_all_older_records():
    ids = [store.create_import(source_type="粘贴", input_text=f"订单{i}") for i in range(55)]
    first = store.list_imports(limit=50)
    second = store.list_imports(limit=50, offset=50)
    assert len(first) == 50 and len(second) == 5
    assert {record["id"] for record in first + second} == set(ids)


@pytest.mark.parametrize("limit,offset", [(0, 0), (1, -1)])
def test_invalid_pagination_is_rejected(limit, offset):
    with pytest.raises(store.HistoryStoreError):
        store.list_imports(limit=limit, offset=offset)


def test_nonfinite_snapshot_is_rejected_without_partial_event():
    record_id = store.create_import(source_type="粘贴", input_text=RAW)
    broken = make_order().model_copy(update={"hourly_rate": float("nan")})
    with pytest.raises(store.HistoryStoreError):
        store.append_event(record_id, kind="解析完成", orders=[broken])
    assert store.get_import(record_id)["events"] == []


def test_chinese_confirmation_names_are_deduplicated():
    assert confirmation_labels(["lesson_hours", "每次时长", "mode", "上课方式", "time_slots", "时间段", "模型解析", "本地解析"]) == ["每次时长", "上课方式", "时间段", "识别结果需核对"]


@pytest.mark.parametrize("criteria,reason", [(Criteria(teacher_gender="男"), "性别"), (Criteria(grades=["高中"]), "年级"), (Criteria(subjects=["物理"]), "科目"), (Criteria(mode="线下"), "方式"), (Criteria(days=[1]), "上课日"), (Criteria(max_weekly_sessions=1), "次数"), (Criteria(time_slots=["上午"]), "时段"), (Criteria(min_hourly_rate=151), "时薪")])
def test_exclusion_reasons_are_kept_for_development(criteria, reason):
    value = make_order("小学数学 150/h 线上 周六晚上 每周2次 要求女老师")
    result = evaluate_order(value, criteria)
    assert result.match_status == "已排除" and reason in result.match_reason
