"""按 Windows 当前用户加密保存模型配置，磁盘与临时文件只写密文。"""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CONFIG_PATH = Path(__file__).resolve().parents[1] / ".local" / "model_config.dpapi"


class ConfigStoreError(RuntimeError):
    """错误信息仅描述操作，不包含密钥或模型配置内容。"""


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str
    api_key: str = Field(repr=False)
    base_url: str
    model_name: str


class SavedSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    active_provider: str = "OpenAI"
    profiles: dict[str, ModelConfig] = Field(default_factory=dict)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise ConfigStoreError("本地加密保存目前仅支持 Windows。")
    if not data:
        raise ConfigStoreError("本地模型配置文件为空。")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    common_args = [blob_pointer, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_pointer]
    protect = crypt32.CryptProtectData
    protect.argtypes = [blob_pointer, wintypes.LPCWSTR, *common_args]
    protect.restype = wintypes.BOOL
    unprotect = crypt32.CryptUnprotectData
    unprotect.argtypes = [blob_pointer, ctypes.POINTER(wintypes.LPWSTR), *common_args]
    unprotect.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data, len(data))
    input_blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    try:
        # 只使用 UI_FORBIDDEN；不使用 LOCAL_MACHINE，以当前 Windows 用户为边界。
        if decrypt:
            success = unprotect(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output_blob))
        else:
            success = protect(ctypes.byref(input_blob), "Tutor_screen model settings", None, None, None, 1, ctypes.byref(output_blob))
        if not success:
            operation = "读取" if decrypt else "加密保存"
            raise ConfigStoreError(f"无法{operation}本地模型配置，请使用保存时的 Windows 账户。")
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.memset(buffer, 0, len(data))
        if output_blob.pbData:
            ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def load_settings() -> SavedSettings:
    try:
        encrypted = CONFIG_PATH.read_bytes()
    except FileNotFoundError:
        return SavedSettings()
    except OSError:
        raise ConfigStoreError("无法读取本地模型配置文件。") from None
    try:
        settings = SavedSettings.model_validate_json(_crypt(encrypted, decrypt=True))
        if any(provider != profile.provider for provider, profile in settings.profiles.items()):
            raise ValueError("配置服务商不一致")
        return settings
    except ConfigStoreError:
        raise
    except (ValueError, TypeError):
        raise ConfigStoreError("本地模型配置格式无效，无法自动加载。") from None


def save_config(config: ModelConfig) -> SavedSettings:
    # 保存当前服务商时，合并磁盘已有的其他服务商配置。
    settings = load_settings()
    settings.active_provider = config.provider
    settings.profiles[config.provider] = config.model_copy(deep=True)
    encrypted = _crypt(settings.model_dump_json().encode("utf-8"), decrypt=False)
    temp_path: Path | None = None
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", dir=CONFIG_PATH.parent, prefix="model_config-", suffix=".tmp", delete=False) as file:
            temp_path = Path(file.name)
            file.write(encrypted)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, CONFIG_PATH)
        return settings
    except OSError:
        raise ConfigStoreError("本地模型配置保存失败，请检查项目目录是否可写；本次会话仍可使用已填配置。") from None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass  # 临时文件也只有密文，清理失败不能掩盖原始保存错误。
