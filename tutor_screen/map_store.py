"""地图设置及使用者确认位置的本机存储，与订单日志生命周期独立。

凭据仅以 Windows 当前用户 DPAPI 密文落盘。接口查询结果默认仅在会话中
缓存；账号拥有存储授权时才开启跨重启缓存，不保存完整订单或原始请求。
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from . import config_store
from .maps import CommuteResult, GeoPoint, GeoResult, MapAPIError, MapCredentials, clean_query


MAP_PATH: Path | None = None
_MEMORY: dict[str, dict[str, dict]] = {}
_MEMORY_LOCK = RLock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id=1), value TEXT NOT NULL, credentials BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS origins (id TEXT PRIMARY KEY, name TEXT NOT NULL, point TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS geocache (id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS manual (id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commutes (id TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class MapStoreError(RuntimeError):
    """安全的本机操作错误，不包含地址、凭据或原始磁盘异常。"""


class MapSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_city: str = Field(default="", max_length=100)
    active_origin_id: str = Field(default="", max_length=64)
    credentials: MapCredentials = Field(default_factory=MapCredentials, repr=False)
    persist_api_cache: bool = False


class SavedOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str = Field(min_length=1, max_length=100)
    point: GeoPoint

    @property
    def label(self) -> str:
        return self.name


def _path() -> Path:
    return Path(MAP_PATH) if MAP_PATH is not None else config_store.CONFIG_PATH.parent / "map_data.sqlite3"


def _memory() -> dict[str, dict]:
    with _MEMORY_LOCK:
        return _MEMORY.setdefault(str(_path().resolve()), {"geo": {}, "commutes": {}})


@contextmanager
def _database():
    connection = None
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA secure_delete=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise MapStoreError("地图本机数据版本不受支持，请保留文件并检查应用版本。")
        if version == 0:
            connection.executescript(_SCHEMA)
            connection.execute("PRAGMA user_version=1")
        yield connection
    except (sqlite3.Error, OSError, ValueError, TypeError, config_store.ConfigStoreError):
        raise MapStoreError("地图本机数据读写失败，请检查目录权限、数据文件和当前 Windows 账户。") from None
    finally:
        if connection is not None:
            connection.close()


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _geo_key(address: str, city: str) -> str:
    # 仅落盘摘要，避免在缓存索引中再次保存原始地址/意外混入的联系方式。
    try:
        address = clean_query(address)
        city = clean_query(city, city=True) if city.strip() else ""
    except (MapAPIError, AttributeError, TypeError):
        raise MapStoreError("订单地点格式需要确认，已保留订单且暂不计算距离；请使用单行城市和地址。") from None
    return _hash(["amap", "GCJ-02", city.casefold(), " ".join(address.split()).casefold()])


def _point_key(point: GeoPoint) -> list:
    checked = GeoPoint.model_validate(point.model_dump())
    # 市字段影响公交规划；标签不影响位置，重命名不应使旧耗时丢失。
    return [round(checked.lng, 6), round(checked.lat, 6), checked.coordinate_system, checked.city]


def _commute_key(origin: GeoPoint, destination: GeoPoint, mode: str) -> str:
    return _hash(["amap", _point_key(origin), _point_key(destination), mode])


def load_settings() -> MapSettings:
    with _database() as connection:
        row = connection.execute("SELECT value, credentials FROM settings WHERE id=1").fetchone()
        if row is None:
            return MapSettings()
        settings = MapSettings.model_validate_json(row["value"])
        if row["credentials"]:
            settings.credentials = MapCredentials.model_validate_json(config_store._crypt(bytes(row["credentials"]), decrypt=True))
        return settings


def save_settings(settings: MapSettings) -> None:
    settings = MapSettings.model_validate(settings.model_dump())
    if settings.default_city.strip():
        try:
            settings.default_city = clean_query(settings.default_city, city=True)
        except MapAPIError:
            raise MapStoreError("默认城市格式无效，请只填写城市名称或城市编码。") from None
    credentials = settings.credentials
    try:
        encrypted = config_store._crypt(credentials.model_dump_json().encode("utf-8"), decrypt=False) if any(credentials.model_dump().values()) else b""
    except config_store.ConfigStoreError:
        raise MapStoreError("地图凭据无法加密保存，请使用当前 Windows 账户；本次会话配置仍可使用。") from None
    safe = settings.model_dump(exclude={"credentials"})
    with _database() as connection, connection:
        connection.execute("INSERT OR REPLACE INTO settings VALUES (1, ?, ?)", (json.dumps(safe, ensure_ascii=False, allow_nan=False), encrypted))
        if not settings.persist_api_cache:
            # 撤回持久缓存授权立即清理磁盘接口数据；会话内结果保留至关闭进程。
            connection.execute("DELETE FROM geocache")
            connection.execute("DELETE FROM commutes")


def list_origins() -> list[SavedOrigin]:
    with _database() as connection:
        return [SavedOrigin(id=row["id"], name=row["name"], point=GeoPoint.model_validate_json(row["point"])) for row in connection.execute("SELECT * FROM origins ORDER BY rowid")]


def save_origin(name: str, point: GeoPoint, origin_id: str = "") -> SavedOrigin:
    name = name.strip()
    if not name or len(name) > 100:
        raise MapStoreError("常用出发点名称应为 1 至 100 个字符。")
    checked = GeoPoint.model_validate(point.model_dump())
    origin = SavedOrigin(id=origin_id or uuid4().hex, name=name, point=checked)
    with _database() as connection, connection:
        connection.execute("INSERT INTO origins VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, point=excluded.point", (origin.id, origin.name, checked.model_dump_json()))
    return origin


def delete_origin(origin_id: str) -> None:
    with _database() as connection, connection:
        connection.execute("DELETE FROM origins WHERE id=?", (origin_id,))
        row = connection.execute("SELECT value FROM settings WHERE id=1").fetchone()
        if row:
            settings = MapSettings.model_validate_json(row["value"])
            if settings.active_origin_id == origin_id:
                settings.active_origin_id = ""
                connection.execute("UPDATE settings SET value=? WHERE id=1", (settings.model_dump_json(exclude={"credentials"}),))


def get_geo(address: str, city: str) -> GeoResult | None:
    key = _geo_key(address, city)
    with _MEMORY_LOCK:
        memory = _memory()["geo"].get(key)
    if memory is not None:
        return GeoResult.model_validate(memory)
    with _database() as connection:
        row = connection.execute("SELECT value FROM geocache WHERE id=?", (key,)).fetchone()
        return GeoResult.model_validate_json(row["value"]) if row else None


def save_geo(address: str, city: str, result: GeoResult) -> None:
    key = _geo_key(address, city)
    checked = GeoResult.model_validate(result.model_dump())
    with _MEMORY_LOCK:
        _memory()["geo"][key] = checked.model_dump()
    if load_settings().persist_api_cache:
        with _database() as connection, connection:
            connection.execute("INSERT OR REPLACE INTO geocache VALUES (?, ?)", (key, checked.model_dump_json()))


def _manual_key(address: str, city: str, order_key: str = "") -> str:
    key = _geo_key(address, city)
    return _hash([key, order_key]) if order_key else key


def get_manual(address: str, city: str, *, order_key: str = "") -> GeoPoint | None:
    key = _manual_key(address, city, order_key)
    with _database() as connection:
        row = connection.execute("SELECT value FROM manual WHERE id=?", (key,)).fetchone()
        return GeoPoint.model_validate_json(row["value"]) if row else None


def save_manual(address: str, city: str, point: GeoPoint, *, order_key: str = "") -> None:
    key = _manual_key(address, city, order_key)
    checked = GeoPoint.model_validate(point.model_dump())
    checked.source = "user"
    with _database() as connection, connection:
        connection.execute("INSERT OR REPLACE INTO manual VALUES (?, ?)", (key, checked.model_dump_json()))


def restore_auto(address: str, city: str, *, order_key: str = "") -> None:
    with _database() as connection, connection:
        connection.execute("DELETE FROM manual WHERE id=?", (_manual_key(address, city, order_key),))


def resolve_geo(address: str, city: str) -> GeoResult | None:
    manual = get_manual(address, city)
    if manual is not None:
        return GeoResult(status="located", candidates=[manual], message="用户确认位置；原始订单地址未改写。")
    return get_geo(address, city)


def get_commute(origin: GeoPoint, dest: GeoPoint, mode: str) -> CommuteResult | None:
    key = _commute_key(origin, dest, mode)
    with _MEMORY_LOCK:
        memory = _memory()["commutes"].get(key)
    if memory is not None:
        return CommuteResult.model_validate(memory)
    with _database() as connection:
        row = connection.execute("SELECT value FROM commutes WHERE id=?", (key,)).fetchone()
        return CommuteResult.model_validate_json(row["value"]) if row else None


def save_commute(origin: GeoPoint, dest: GeoPoint, result: CommuteResult) -> None:
    checked = CommuteResult.model_validate(result.model_dump())
    key = _commute_key(origin, dest, checked.mode)
    with _MEMORY_LOCK:
        _memory()["commutes"][key] = checked.model_dump()
    if load_settings().persist_api_cache:
        with _database() as connection, connection:
            connection.execute("INSERT OR REPLACE INTO commutes VALUES (?, ?)", (key, checked.model_dump_json()))


def clear_cache() -> None:
    with _MEMORY_LOCK:
        _MEMORY.pop(str(_path().resolve()), None)
    with _database() as connection:
        with connection:
            connection.execute("DELETE FROM geocache")
            connection.execute("DELETE FROM commutes")
        try:
            connection.execute("VACUUM")
        except sqlite3.Error:
            pass


def stats() -> dict[str, int]:
    with _database() as connection:
        result = {"origins": connection.execute("SELECT COUNT(*) FROM origins").fetchone()[0], "manual": connection.execute("SELECT COUNT(*) FROM manual").fetchone()[0]}
        for table, bucket in (("geocache", "geo"), ("commutes", "commutes")):
            disk = {row[0] for row in connection.execute(f"SELECT id FROM {table}")}
            with _MEMORY_LOCK:
                result[bucket] = len(disk | set(_memory()[bucket]))
        return result
