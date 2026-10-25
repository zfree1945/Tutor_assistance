"""本轮测试独立配置；不会读取或改写日常模型密钥。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr("tutor_screen.config_store.CONFIG_PATH", tmp_path / "config.dpapi")
    monkeypatch.setattr("tutor_screen.map_store.MAP_PATH", tmp_path / "map_data.sqlite3")
    # 未显式模拟的调用一律禁止访问真实模型服务。
    monkeypatch.setattr("tutor_screen.llm_client._client", lambda *_: (_ for _ in ()).throw(AssertionError("禁止真实 API 请求")))
