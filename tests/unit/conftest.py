import pytest


@pytest.fixture(autouse=True)
def isolated_model_config(monkeypatch, tmp_path):
    """任何测试都不能读取或改写用户实际保存的模型密钥。"""
    monkeypatch.setattr("tutor_screen.config_store.CONFIG_PATH", tmp_path / ".local" / "model_config.dpapi")
    monkeypatch.setattr("tutor_screen.history_store.HISTORY_PATH", tmp_path / ".local" / "order_history.sqlite3")
    monkeypatch.setattr("tutor_screen.map_store.MAP_PATH", tmp_path / ".local" / "map_data.sqlite3")
