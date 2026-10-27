"""QA-18: bounded subprocess; measurements apply only to local mock work."""
import json
from pathlib import Path
import subprocess
import sys

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts" / "acceptance" / "verification_20261015" / "load"
ROOT = Path(__file__).resolve().parents[2]


def test_qa18_batch_long_order_and_growing_event_history_are_bounded(tmp_path):
    script = '''import json,sys,time
from pathlib import Path
from unittest.mock import patch
from tutor_screen import history_store as s
from tutor_screen.local_parser import parse_local
from tutor_screen.pipeline import process_batch
from tutor_screen.matching import apply_criteria
from tutor_screen.models import Criteria
s.HISTORY_PATH=Path(sys.argv[1])
measurements={}
raw="\\n".join(f"订单号 QA{i:03d} 小学数学 150/h 线上 周日晚上 每周1次" for i in range(300))
begin=time.perf_counter()
with patch("tutor_screen.pipeline.extract_order",side_effect=lambda text,*_:parse_local(text)):
    orders=process_batch(raw,"SYNTHETIC","https://example.test","mock")
assert len(orders)==300 and len({o.order_id for o in orders})==300
assert "\\n".join(o.raw_text for o in orders)==raw
measurements["batch_300_seconds"]=time.perf_counter()-begin
begin=time.perf_counter()
for i in range(10): assert len(apply_criteria(orders,Criteria(min_hourly_rate=151)))==0
measurements["filter_300_x10_seconds"]=time.perf_counter()-begin
long_raw="订单号 QA999 小学数学 150/h 线上 周日晚上 每周1次\\n备注："+"合成说明"*25000
begin=time.perf_counter()
with patch("tutor_screen.pipeline.extract_order",side_effect=lambda text,*_:parse_local(text)):
    long_orders=process_batch(long_raw,"SYNTHETIC","https://example.test","mock")
assert len(long_orders)==1 and long_orders[0].raw_text==long_raw
measurements["long_order_characters"]=len(long_raw)
measurements["long_order_seconds"]=time.perf_counter()-begin
record=s.create_import(source_type="粘贴",input_text=orders[0].raw_text)
begin=time.perf_counter()
for i in range(1000):
    s.append_event(record,kind="synthetic",orders=orders[:1])
    if i+1 in {10,100,1000}:
        read_begin=time.perf_counter()
        assert len(s.get_import(record)["events"])==i+1
        measurements[f"read_{i+1}_events_seconds"]=time.perf_counter()-read_begin
measurements["write_1000_events_seconds"]=time.perf_counter()-begin
print(json.dumps(measurements))
'''
    result = subprocess.run([sys.executable,"-X","utf8","-c",script,str(tmp_path/"load.sqlite3")],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=60,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    assert result.returncode == 0,result.stderr
    measurements = json.loads(result.stdout)
    assert measurements["long_order_characters"] > 100000
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    (ARTIFACTS/"performance.json").write_text(json.dumps({"scope":"Offline local rules, mocked extraction, wall clock; 60-second child-process timeout",**measurements},indent=2),encoding="utf-8")
