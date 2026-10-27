import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tutor_screen import config_store
from tutor_screen.config_store import ConfigStoreError, ModelConfig, load_settings, save_config


APP_PATH = Path(__file__).resolve().parents[2] / "app.py"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="实际 DPAPI 加解密需要 Windows")


def sample_config(provider="DeepSeek", key="dummy-local-storage-key") -> ModelConfig:
    return ModelConfig(provider=provider, api_key=key, base_url="https://example.test/v1", model_name="test-model")


def test_real_encrypted_roundtrip_and_restore_from_another_process():
    config = sample_config()
    save_config(config)
    assert config.api_key.encode() not in config_store.CONFIG_PATH.read_bytes()
    assert load_settings().profiles["DeepSeek"] == config
    assert config.api_key not in repr(config)
    assert not list(config_store.CONFIG_PATH.parent.glob("*.tmp"))
    code = """from pathlib import Path
import sys
from tutor_screen import config_store
config_store.CONFIG_PATH = Path(sys.argv[1])
profile = config_store.load_settings().profiles['DeepSeek']
assert profile.api_key == 'dummy-local-storage-key'
assert profile.model_name == 'test-model'
print('restored')
"""
    result = subprocess.run([sys.executable, "-c", code, str(config_store.CONFIG_PATH)], cwd=APP_PATH.parent, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "restored"


def test_saved_providers_keep_separate_keys_and_active_provider():
    save_config(sample_config("DeepSeek", "dummy-deepseek"))
    save_config(sample_config("OpenAI", "dummy-openai"))
    settings = load_settings()
    assert settings.active_provider == "OpenAI"
    assert settings.profiles["DeepSeek"].api_key == "dummy-deepseek"
    assert settings.profiles["OpenAI"].api_key == "dummy-openai"


def test_failed_replace_keeps_previous_encrypted_file():
    save_config(sample_config())
    previous = config_store.CONFIG_PATH.read_bytes()
    with patch("tutor_screen.config_store.os.replace", side_effect=PermissionError("test failure")):
        with pytest.raises(ConfigStoreError):
            save_config(sample_config(key="dummy-new-key"))
    assert config_store.CONFIG_PATH.read_bytes() == previous
    assert not list(config_store.CONFIG_PATH.parent.glob("*.tmp"))


def test_invalid_file_reports_generic_error_without_overwriting_it():
    config_store.CONFIG_PATH.parent.mkdir(parents=True)
    original = b"invalid file with dummy-secret"
    config_store.CONFIG_PATH.write_bytes(original)
    with pytest.raises(ConfigStoreError) as error:
        load_settings()
    assert "dummy-secret" not in str(error.value)
    with pytest.raises(ConfigStoreError):
        save_config(sample_config())
    assert config_store.CONFIG_PATH.read_bytes() == original


def test_already_entered_session_configuration_is_saved_and_new_session_restores_it():
    app = AppTest.from_file(APP_PATH)
    app.session_state["provider"] = "DeepSeek"
    app.session_state["api_key"] = "dummy-existing-session-key"
    app.session_state["base_url"] = "https://example.test/v1"
    app.session_state["model_name"] = "test-model"
    app.run()
    assert not app.exception
    assert load_settings().profiles["DeepSeek"].api_key == "dummy-existing-session-key"
    reopened = AppTest.from_file(APP_PATH).run()
    assert not reopened.exception
    assert reopened.session_state["provider"] == "DeepSeek"
    assert reopened.session_state["api_key"] == "dummy-existing-session-key"
    assert reopened.session_state["base_url"] == "https://example.test/v1"
    assert reopened.session_state["model_name"] == "test-model"
    assert not any("尚未配置 API Key" in info.value for info in reopened.info)
    reopened.radio(key="nav_widget").set_value("模型设置").run()
    assert reopened.text_input(key="api_key").value == "dummy-existing-session-key"


def test_deepseek_settings_save_automatically_and_restore_after_restart():
    app = AppTest.from_file(APP_PATH).run()
    app.radio(key="nav_widget").set_value("模型设置").run()
    assert app.selectbox(key="provider").options == ["DeepSeek"]
    assert app.selectbox(key="provider").disabled
    app.text_input(key="api_key").set_value("dummy-deepseek").run()
    app.text_input(key="base_url").set_value("https://example.test/deepseek").run()
    app.text_input(key="model_name").set_value("test-deepseek-model").run()
    assert not app.exception
    assert app.text_input(key="api_key").value == "dummy-deepseek"
    assert app.text_input(key="base_url").value == "https://example.test/deepseek"
    assert app.text_input(key="model_name").value == "test-deepseek-model"
    reopened = AppTest.from_file(APP_PATH).run()
    assert reopened.session_state["provider"] == "DeepSeek"
    assert reopened.session_state["api_key"] == "dummy-deepseek"
    reopened.radio(key="nav_widget").set_value("模型设置").run()
    reopened.text_input(key="api_key").set_value("").run()
    assert load_settings().profiles["DeepSeek"].api_key == ""
    assert AppTest.from_file(APP_PATH).run().session_state["api_key"] == ""


@pytest.mark.parametrize("has_deepseek", [False, True])
def test_legacy_active_provider_restores_only_deepseek_credentials(has_deepseek):
    if has_deepseek:
        save_config(sample_config("DeepSeek", "dummy-deepseek"))
    save_config(sample_config("OpenAI", "dummy-legacy-key"))
    app = AppTest.from_file(APP_PATH).run()
    assert not app.exception
    assert app.session_state["provider"] == "DeepSeek"
    assert app.session_state["api_key"] == ("dummy-deepseek" if has_deepseek else "")
    if not has_deepseek:
        assert app.session_state["base_url"] == "https://api.deepseek.com"
        assert app.session_state["model_name"] == "deepseek-chat"
    assert load_settings().profiles["OpenAI"].api_key == "dummy-legacy-key"


@pytest.mark.parametrize("has_deepseek", [False, True])
@pytest.mark.parametrize("already_loaded", [False, True])
def test_hot_reload_replaces_legacy_session_credentials(has_deepseek, already_loaded):
    if has_deepseek:
        save_config(sample_config("DeepSeek", "dummy-deepseek"))
    app = AppTest.from_file(APP_PATH)
    app.session_state["provider"] = "OpenAI"
    app.session_state["api_key"] = "dummy-legacy-session-key"
    app.session_state["base_url"] = "https://example.test/legacy"
    app.session_state["model_name"] = "legacy-model"
    if already_loaded:
        app.session_state["_model_config_loaded"] = True
        app.session_state["_saved_model_profiles"] = load_settings().profiles
        app.session_state["_model_config_error"] = None
    app.run()
    assert not app.exception
    assert app.session_state["provider"] == "DeepSeek"
    assert app.session_state["api_key"] == ("dummy-deepseek" if has_deepseek else "")
    assert app.session_state["base_url"] == ("https://example.test/v1" if has_deepseek else "https://api.deepseek.com")


def test_model_help_has_deepseek_creation_link_after_first_heading():
    app = AppTest.from_file(APP_PATH).run()
    assert app.session_state["provider"] == "DeepSeek"
    assert app.session_state["base_url"] == "https://api.deepseek.com"
    app.radio(key="nav_widget").set_value("模型设置").run()
    assert not app.exception
    markdown = [node.value for node in app.markdown]
    index = markdown.index("**01 · 快速创建deepseek API**")
    assert markdown[index + 1] == "[前往 DeepSeek 创建 API Key](https://platform.deepseek.com/api_keys)"
    visible = "\n".join(markdown + [node.value for node in app.caption])
    assert "填写 API Key 后自动保存 DeepSeek 配置。" in visible
    for old_text in ["选择服务商", "切换服务商", "豆包", "自定义接口"]:
        assert old_text not in visible


def test_save_failure_is_visible_and_keeps_configuration_in_session():
    app = AppTest.from_file(APP_PATH).run()
    app.radio(key="nav_widget").set_value("模型设置").run()
    with patch("tutor_screen.config_store.save_config", side_effect=ConfigStoreError("测试：目录不可写")):
        app.text_input(key="api_key").set_value("dummy-session-key").run()
    assert not app.exception
    assert app.session_state["api_key"] == "dummy-session-key"
    assert any("目录不可写" in error.value for error in app.error)
    assert not any("已自动保存" in caption.value for caption in app.caption)
    assert not config_store.CONFIG_PATH.exists()
