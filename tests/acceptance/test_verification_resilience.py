"""QA-11..14: synthetic secrets, real temporary SQLite and child processes."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tutor_screen import config_store, history_store as store, llm_client
from tutor_screen.export import orders_csv
from tutor_screen.models import Criteria, Order
from tutor_screen.pipeline import process_batch

ROOT = Path(__file__).resolve().parents[2]
RAW = "订单号 QA101 小学数学 150/h 线上 周日晚上 每周1次"
KEY = "SYNTHETIC-CONFIG-KEY-DO-NOT-EXPORT"


@pytest.mark.parametrize("route", ["extract", "split"])
def test_qa11_both_request_paths_mask_private_data_and_do_not_send_key_in_body(monkeypatch, route):
    raw = RAW + "\n地址：测试市合成路999号\n电话：13800138000\n微信：qa_private_123"
    reply = '{}' if route == "extract" else '{"boundaries":[]}'
    create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))]))
    factory = Mock(return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(llm_client, "_client", factory)
    if route == "extract":
        assert llm_client.extract_order(raw,KEY,"https://example.test","mock").address is None
    else:
        assert llm_client.try_split(raw,KEY,"https://example.test","mock") is None
    factory.assert_called_once_with(KEY,"https://example.test")
    body = json.dumps(create.call_args.kwargs,ensure_ascii=False)
    assert all(secret not in body for secret in [KEY,"测试市合成路999号","13800138000","qa_private_123"])
    assert "小学数学" in body and "150/h" in body


def test_qa11_provider_exception_does_not_leak_to_csv_or_history(monkeypatch):
    monkeypatch.setattr("tutor_screen.pipeline.extract_order",Mock(side_effect=RuntimeError(KEY)))
    orders = process_batch(RAW,KEY,"https://example.test","mock")
    assert len(orders) == 1 and orders[0].raw_text == RAW
    record_id = store.create_import(source_type="粘贴",input_text=RAW)
    store.append_event(record_id,kind="解析完成",orders=orders)
    assert KEY not in orders_csv(orders).decode("utf-8-sig")
    assert KEY not in json.dumps(store.get_import(record_id),ensure_ascii=False)


@pytest.mark.parametrize("count", [50,51,100,101])
def test_qa14_all_pagination_boundaries(count):
    ids = {store.create_import(source_type="粘贴",input_text=f"synthetic-{i}") for i in range(count)}
    pages = [store.list_imports(offset=offset) for offset in range(0,count,50)]
    assert len([r for page in pages for r in page]) == count
    assert {r["id"] for page in pages for r in page} == ids


def test_qa14_future_schema_preserves_file():
    store.create_import(source_type="粘贴",input_text=RAW)
    with sqlite3.connect(store.HISTORY_PATH) as db:
        db.execute("PRAGMA user_version=99")
    before = store.HISTORY_PATH.read_bytes()
    with pytest.raises(store.HistoryStoreError,match="版本"):
        store.count_imports()
    assert store.HISTORY_PATH.read_bytes() == before


def test_qa14_old_snapshot_and_corrupt_neighbor_are_independent():
    good = store.create_import(source_type="粘贴",input_text=RAW)
    bad = store.create_import(source_type="粘贴",input_text="synthetic broken neighbor")
    legacy = Order(order_id="legacy",raw_text=RAW,grade="小学",subjects=["数学"])
    store.append_event(good,kind="legacy",orders=[legacy])
    store.append_event(bad,kind="broken",orders=[legacy])
    payload = legacy.model_dump(mode="json")
    payload.pop("schedule_windows")
    with sqlite3.connect(store.HISTORY_PATH) as db:
        db.execute("UPDATE events SET orders_json=? WHERE import_id=?",(json.dumps([payload]),good))
        db.execute("UPDATE events SET orders_json='not-json' WHERE import_id=?",(bad,))
    before = hashlib.sha256(store.HISTORY_PATH.read_bytes()).hexdigest()
    assert store.get_import(good)["events"][0]["orders"][0]["schedule_windows"] == []
    with pytest.raises(store.HistoryStoreError):
        store.get_import(bad)
    assert store.count_imports() == 2
    assert hashlib.sha256(store.HISTORY_PATH.read_bytes()).hexdigest() == before


def child(script, *args):
    return subprocess.Popen([sys.executable,"-X","utf8","-c",script,str(store.HISTORY_PATH),*args],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8",creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))


def test_qa12_two_processes_append_and_read_without_lost_events():
    record_id = store.create_import(source_type="粘贴",input_text=RAW)
    script = '''import sys
from pathlib import Path
from tutor_screen import history_store as s
s.HISTORY_PATH=Path(sys.argv[1])
for i in range(12):
    s.append_event(sys.argv[2],kind=sys.argv[3]+str(i))
    assert s.get_import(sys.argv[2])["events"]
'''
    processes = [child(script,record_id,prefix) for prefix in ["A","B"]]
    try:
        for process in processes:
            stdout,stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    events = store.get_import(record_id)["events"]
    assert len(events) == 24
    assert {e["kind"] for e in events} == {p+str(i) for p in ["A","B"] for i in range(12)}


def test_qa13_real_write_lock_times_out_then_recovers():
    record_id = store.create_import(source_type="粘贴",input_text=RAW)
    with sqlite3.connect(store.HISTORY_PATH) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        with pytest.raises(store.HistoryStoreError):
            store.append_event(record_id,kind="must-not-commit")
        lock.rollback()
    assert store.get_import(record_id)["events"] == []
    store.append_event(record_id,kind="retry")
    assert [e["kind"] for e in store.get_import(record_id)["events"]] == ["retry"]


def test_qa13_disk_failure_injection_keeps_committed_data_and_configuration(monkeypatch):
    record_id = store.create_import(source_type="粘贴",input_text=RAW)
    config_store.CONFIG_PATH.write_bytes(b"SYNTHETIC-OPAQUE-CONFIG")
    before = store.HISTORY_PATH.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(store.sqlite3,"connect",Mock(side_effect=sqlite3.OperationalError("disk full "+KEY)))
        with pytest.raises(store.HistoryStoreError) as error:
            store.append_event(record_id,kind="must-not-save")
    assert KEY not in str(error.value)
    assert store.HISTORY_PATH.read_bytes() == before
    assert config_store.CONFIG_PATH.read_bytes() == b"SYNTHETIC-OPAQUE-CONFIG"
    assert store.get_import(record_id)["events"] == []


def test_qa13_process_exit_rolls_back_uncommitted_work_and_keeps_processing_status():
    record_id = store.create_import(source_type="粘贴",input_text=RAW)
    store.append_event(record_id,kind="开始解析",status="解析中")
    script = '''import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
db.execute("UPDATE imports SET status='已解析' WHERE id=?",(sys.argv[2],))
os._exit(17)
'''
    process = child(script,record_id)
    process.communicate(timeout=15)
    assert process.returncode == 17
    record = store.get_import(record_id)
    assert record["status"] == "解析中"
    assert [e["kind"] for e in record["events"]] == ["开始解析"]


def test_qa12_cleared_id_cannot_be_resurrected_from_another_process():
    record_id = store.create_import(source_type="粘贴",input_text=RAW)
    store.clear_history()
    process = child('''import sys
from pathlib import Path
from tutor_screen import history_store as s
s.HISTORY_PATH=Path(sys.argv[1])
try: s.append_event(sys.argv[2],kind="stale")
except s.HistoryStoreError: sys.exit(0)
sys.exit(1)
''',record_id)
    _,stderr = process.communicate(timeout=15)
    assert process.returncode == 0,stderr
    assert store.count_imports() == 0
