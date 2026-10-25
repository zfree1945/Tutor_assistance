"""独立保存最近成功解析的批次；替换文件时不破坏上一份完整快照。"""

import os
from pathlib import Path
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import config_store, history_store
from .models import Criteria, Order


class BatchStoreError(RuntimeError):
    """不包含订单原文或底层异常参数的本地保存错误。"""


class SavedBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    input_text: str
    orders: list[Order] = Field(min_length=1, max_length=300)
    criteria: Criteria = Field(default_factory=Criteria)
    import_id: str | None = None


def batch_path() -> Path:
    return config_store.CONFIG_PATH.parent / "current_batch.json"


def load_batch() -> SavedBatch | None:
    try:
        path = batch_path()
        if path.exists():
            return SavedBatch.model_validate_json(path.read_bytes())
        # 升级前已解析的订单从日志恢复；未解析或失败的导入不覆盖成功批次。
        previous = history_store.latest_parsed_batch()
        return SavedBatch.model_validate(previous) if previous else None
    except (OSError, ValueError, history_store.HistoryStoreError):
        raise BatchStoreError("本地订单恢复失败，原保存文件已保留，请检查磁盘或文件内容。") from None


def save_batch(batch: SavedBatch) -> None:
    temporary = None
    try:
        batch = SavedBatch.model_validate(batch.model_dump(mode="json"))
        payload = batch.model_dump_json(indent=2).encode("utf-8")
        path = batch_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".batch-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError):
        raise BatchStoreError("本地订单保存失败；当前订单仍保留在页面中，上一份本地快照未替换。") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
