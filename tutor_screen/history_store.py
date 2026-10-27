"""持久化导入和筛选开发日志；不接收模型配置或认证信息。"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from . import config_store
from .models import Criteria, Order

# 默认使用应用本地数据目录；测试/独立工具可显式覆盖。
HISTORY_PATH: Path | None = None
SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, source_type TEXT NOT NULL,
    filename TEXT, input_text TEXT, file_content BLOB, file_size INTEGER,
    file_sha256 TEXT, status TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id TEXT NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, kind TEXT NOT NULL, criteria_json TEXT NOT NULL,
    orders_json TEXT NOT NULL, parsed_json TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_import ON events(import_id, id);
"""


class HistoryStoreError(RuntimeError):
    """可展示的操作错误，不携带原始异常参数。"""


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="microseconds")


def _json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise HistoryStoreError("订单快照格式无效，日志未保存；当前订单仍保留在会话中。") from None


@contextmanager
def _database():
    connection = None
    try:
        path = HISTORY_PATH or config_store.CONFIG_PATH.parent / "order_history.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA secure_delete=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise HistoryStoreError("本地日志版本不受支持，请保留原文件并检查应用版本。")
        if version == 0:
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version=1")
        yield connection
    except (sqlite3.Error, OSError, ValueError, TypeError):
        raise HistoryStoreError("本地日志读写失败，请检查磁盘、目录权限或日志文件；当前订单仍保留在会话中。") from None
    finally:
        if connection is not None:
            connection.close()


def create_import(*, source_type: str, input_text: str | None, filename: str | None = None,
                  file_content: bytes | None = None, note: str = "") -> str:
    if source_type not in {"粘贴", "TXT", "会话补存"}:
        raise HistoryStoreError("导入日志的来源类型无效。")
    import_id = uuid4().hex
    digest = hashlib.sha256(file_content).hexdigest() if file_content is not None else None
    with _database() as connection, connection:
        connection.execute("INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (import_id, _now(), source_type, filename, input_text, file_content,
                            len(file_content) if file_content is not None else None, digest,
                            "未解析" if not note else "导入待检查", note))
    return import_id


def append_event(import_id: str, *, kind: str, criteria: Criteria | None = None,
                 orders: list[Order] | None = None, parsed_orders: list[Order] | None = None,
                 status: str = "已解析", note: str = "") -> None:
    payload = _json([order.model_dump(mode="json") for order in orders or []])
    parsed = _json([order.model_dump(mode="json") for order in parsed_orders or []])
    criteria_json = _json(criteria.model_dump(mode="json") if criteria else {})
    with _database() as connection, connection:
        if not connection.execute("SELECT 1 FROM imports WHERE id=?", (import_id,)).fetchone():
            raise HistoryStoreError("这条导入日志已被清空，请重新导入以建立新记录。")
        connection.execute("INSERT INTO events (import_id, created_at, kind, criteria_json, orders_json, parsed_json, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (import_id, _now(), kind, criteria_json, payload, parsed, note))
        connection.execute("UPDATE imports SET status=?, note=? WHERE id=?", (status, note, import_id))


def count_imports() -> int:
    with _database() as connection:
        return connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0]


def latest_parsed_batch() -> dict | None:
    """读取最近一次包含解析结果的快照，供升级前批次恢复使用。"""
    with _database() as connection:
        row = connection.execute(
            "SELECT imports.id, imports.input_text, events.criteria_json, events.parsed_json "
            "FROM events JOIN imports ON imports.id=events.import_id "
            "WHERE events.parsed_json <> '[]' ORDER BY events.id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        orders = [Order.model_validate(value) for value in json.loads(row["parsed_json"])]
        return {
            "import_id": row["id"],
            "input_text": row["input_text"] if row["input_text"] is not None else "\n\n".join(order.raw_text for order in orders),
            "orders": [order.model_dump(mode="json") for order in orders],
            "criteria": Criteria.model_validate_json(row["criteria_json"]).model_dump(mode="json"),
        }


def has_import(import_id: str) -> bool:
    with _database() as connection:
        return connection.execute("SELECT 1 FROM imports WHERE id=?", (import_id,)).fetchone() is not None


def list_imports(*, limit: int = 50, offset: int = 0) -> list[dict]:
    if limit < 1 or offset < 0:
        raise HistoryStoreError("日志分页参数无效。")
    with _database() as connection:
        rows = connection.execute("SELECT id, created_at, source_type, filename, file_size, status, note FROM imports ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [dict(row) for row in rows]


def get_import(import_id: str) -> dict | None:
    with _database() as connection:
        row = connection.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        events = []
        for event in connection.execute("SELECT * FROM events WHERE import_id=? ORDER BY id", (import_id,)):
            value = dict(event)
            value["criteria"] = json.loads(value.pop("criteria_json"))
            value["orders"] = json.loads(value.pop("orders_json"))
            value["parsed_orders"] = json.loads(value.pop("parsed_json"))
            if not isinstance(value["orders"], list) or not isinstance(value["parsed_orders"], list):
                raise ValueError("日志订单快照不是数组")
            value["criteria"] = Criteria.model_validate(value["criteria"]).model_dump(mode="json")
            value["orders"] = [Order.model_validate(order).model_dump(mode="json") for order in value["orders"]]
            value["parsed_orders"] = [Order.model_validate(order).model_dump(mode="json") for order in value["parsed_orders"]]
            events.append(value)
        result["events"] = events
        return result


def clear_history() -> None:
    with _database() as connection:
        with connection:
            connection.execute("DELETE FROM events")
            connection.execute("DELETE FROM imports")
        try:
            connection.execute("VACUUM")
        except sqlite3.Error:
            # DELETE事务已提交，secure_delete已清除内容；空间回收失败不应误报删除失败。
            pass
