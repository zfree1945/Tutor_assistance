"""高德官方 REST 接口；订单定位由已应用筛选触发。

不接受完整订单、任意 URL 或其他服务商坐标；直线距离完全在本机计算。
官方参数：https://lbs.amap.com/api/webservice/guide/api/georegeo
https://lbs.amap.com/api/webservice/guide/api/direction
"""
from __future__ import annotations

import math
import re
import time
from threading import Lock
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .address_rules import NEARBY as _NEARBY
from .address_rules import valid_address
from .address_parts import split_address, county_city_parent


MODES = ("公共交通", "骑行", "步行", "驾车")
LOCATION_REVISION = 10
_PATHS = {
    "geocode": "/v3/geocode/geo",
    "search": "/v3/place/text",
    "公共交通": "/v3/direction/transit/integrated",
    "骑行": "/v4/direction/bicycling",
    "步行": "/v3/direction/walking",
    "驾车": "/v3/direction/driving",
}
# 包含日额度、账号额度、QPS、IP 额度及余额；任何限流均停止批量查询。
_LIMIT_CODES = {
    "10003", "10004", "10010", "10014", "10015", "10019", "10020",
    "10021", "10029", "10044", "10045", "40000", "40002", "40003",
}
_AUTH_CODES = {"10001", "10002", "10005", "10006", "10007", "10008", "10009", "10012", "10013", "10026", "10041"}
_COARSE_LEVELS = {"国家", "省", "市", "区县", "开发区", "乡镇", "街道", "社区", "村庄", "热点商圈", "道路", "未知"}
_REQUEST_LOCK = Lock()
_NEXT_REQUEST_AT = 0.0
_REQUEST_INTERVAL = 1.0


class GeoPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    label: str = Field(default="", max_length=300)
    city: str = Field(default="", max_length=100)
    city_code: str = Field(default="", pattern=r"^(?:\d{3,4})?$")
    district: str = Field(default="", max_length=100)
    adcode: str = Field(default="", pattern=r"^(?:\d{6})?$")
    poi_id: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=300)
    precision: Literal["exact", "approximate", "area"] = "approximate"
    area_scale: Literal["small", "medium", "large"] | None = None
    source: Literal["amap", "user"] = "amap"
    coordinate_system: Literal["GCJ-02"] = "GCJ-02"


class MapCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    js_key: str = Field(default="", repr=False, max_length=200)
    security_code: str = Field(default="", repr=False, max_length=200)
    web_key: str = Field(default="", repr=False, max_length=200)


class GeoResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["located", "ambiguous", "not_found", "failed"]
    candidates: list[GeoPoint] = Field(default_factory=list, max_length=25)
    area_point: GeoPoint | None = None
    message: str = Field(default="", max_length=500)
    resolver_revision: int = Field(default=1, ge=1)

    @field_validator("area_point")
    @classmethod
    def area_precision(cls, value: GeoPoint | None) -> GeoPoint | None:
        return value.model_copy(update={"precision": "area"}) if value else None


class CommuteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["公共交通", "骑行", "步行", "驾车"]
    minutes: float = Field(ge=0, le=100_000)
    distance_m: float = Field(ge=0, le=100_000_000)
    summary: str = Field(default="", max_length=300)


class MapAPIError(RuntimeError):
    """只携带可展示的固定文本，不暴露 URL、凭据或上游返回内容。"""

    def __init__(self, message: str, *, quota_exhausted: bool = False,
                 stop_batch: bool = True, code: str = "", endpoint: str = ""):
        self.code = str(code) if re.fullmatch(r"\d{1,6}", str(code)) else ""
        self.endpoint = endpoint if endpoint in _PATHS else ""
        details = []
        if self.endpoint:
            details.append("接口：" + {"geocode": "地址解析", "search": "地点搜索"}.get(endpoint, endpoint))
        if self.code:
            details.append("错误码：" + self.code)
        if details:
            message += "（" + "，".join(details) + "）"
        super().__init__(message)
        self.quota_exhausted = quota_exhausted
        self.stop_batch = stop_batch or quota_exhausted


def clean_query(value: str, *, city: bool = False) -> str:
    """去掉意外混入地点字段的联系方式，拒绝整段原文或过长输入。"""
    if not isinstance(value, str):
        raise MapAPIError("请填写有效的城市或地点。")
    # 不允许把多行订单透传到地图服务。
    if "\n" in value.strip() or "\r" in value.strip():
        raise MapAPIError("地点应为单行城市或地址，请勿填写完整订单。")
    # 地址提取偶有把同一行的后续订单字段带上；从字段边界截断，绝不透传。
    value = re.split(r"(?:【|\[)?(?:课酬|薪资|报酬|时薪|费用|要求|科目|年级|辅导内容|上课时间|每周|微信(?:号)?|[Vv][Xx]|[Ww]echat|手机号?|联系电话|电话|联系人|联系方式)\s*(?:】|\])?\s*[:：]?", value, maxsplit=1)[0]
    value = re.split(r"\d+(?:\.\d+)?\s*元\s*(?:/|每)?\s*(?:小时|时|课时|次)", value, maxsplit=1)[0]
    value = re.sub(r"(?<!\d)(?:\+?86[ -]*)?1[3-9](?:[ -]*\d){9}(?!\d)", "", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ，,；;：:")
    value = re.sub(r"^(?:(?:【|\[)(?:上课地点|地址|地点|位置|住址)(?:】|\])\s*[:：]?|(?:上课地点|地址|地点|位置|住址)\s*[:：])\s*", "", value)
    if not value or len(value) > (100 if city else 160):
        raise MapAPIError("地点为空或过长，请仅填写定位所需的城市、地址。")
    return value


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _point(location, *, label: str, city: str, precision: str = "approximate",
           district: str = "", adcode: str = "", poi_id: str = "", name: str = "", city_code: str = "") -> GeoPoint | None:
    if not isinstance(location, str):
        return None
    try:
        longitude, latitude = location.split(",")
        return GeoPoint(lng=float(longitude), lat=float(latitude), label=label[:300], city=city[:100], precision=precision,
                        district=district[:100], adcode=adcode if re.fullmatch(r"\d{6}", adcode) else "",
                        poi_id=poi_id[:100], name=name[:300], city_code=city_code if re.fullmatch(r"\d{3,4}", city_code) else "")
    except (ValueError, TypeError, ValidationError):
        return None


def _distinct(points: list[GeoPoint]) -> list[GeoPoint]:
    found: dict[tuple, GeoPoint] = {}
    for point in points:
        # 同名地点有多个出口时不合并；只删除同标签、同坐标的重复返回。
        found.setdefault((point.poi_id, round(point.lng, 6), round(point.lat, 6), point.label,
                          point.city, point.district, point.adcode), point)
    return list(found.values())


def _geocode_point(item: dict) -> GeoPoint | None:
    city = _text(item.get("city"))
    province = _text(item.get("province"))
    return _point(item.get("location"), label=_text(item.get("formatted_address")),
                  city=city or (province if province.endswith("市") else ""),
                  district=_text(item.get("district")), adcode=_text(item.get("adcode")),
                  city_code=_text(item.get("citycode")),
                  precision="exact" if _text(item.get("level")) in {"门牌号", "单元号", "门址"} else "approximate")


def _district_hint(address: str) -> str:
    return split_address(address).district


def _landmark(address: str, city: str = "") -> str:
    parts = split_address(address, city)
    value = parts.landmark
    # 有效地名不依赖“园/城/筑”等字；纯区域、门牌和泛词不作为地标。
    if len(value) < 2 or not valid_address(value) or re.fullmatch(r".*(?:地址|地点|商圈|交叉口)|学校|小区|.*(?:社区|街道|开发区)", value):
        return ""
    if re.fullmatch(r"(?:[初高][一二三1-3]|[一二三四五六1-6]年级)(?:语文|数学|英语|物理|化学|全科)+", value):
        return ""
    return value


def _same_city(point: GeoPoint, address: str, city: str) -> bool:
    hint = split_address(address, city).city.removesuffix("市")
    returned_city = point.city or split_address(point.label).city
    returned = (county_city_parent(returned_city) or returned_city).removesuffix("市")
    if hint.isdigit():
        if len(hint) == 6:
            prefix = hint[:2] if hint.endswith("0000") else hint[:4] if hint.endswith("00") else hint
            return bool(point.adcode and point.adcode.startswith(prefix))
        return bool(point.city_code and point.city_code == hint)
    return bool(returned and (not hint or hint == returned))


def _district_matches(point: GeoPoint, address: str, city: str, adcode: str = "") -> bool:
    district = split_address(address, city).district
    returned = point.district or split_address(point.label, point.city).district
    if adcode and point.adcode and point.adcode != adcode:
        return False
    if district and returned and returned != district:
        return False
    return not district or returned == district or bool(adcode and point.adcode == adcode)


def _school_number_text(number: int) -> str:
    digits = "零一二三四五六七八九"
    if number < 10:
        return digits[number]
    if number < 100:
        return (digits[number // 10] if number >= 20 else "") + "十" + (digits[number % 10] if number % 10 else "")
    tail = number % 100
    suffix = ("零" + digits[tail] if 0 < tail < 10 else _school_number_text(tail) if tail else "")
    if 10 <= tail < 20:
        suffix = "一" + suffix
    return digits[number // 100] + "百" + suffix


def _numbered_school(value: str) -> tuple[str, str] | None:
    value = re.sub(r"\s+", "", value)
    match = re.search(r"(?<![0-9一二三四五六七八九十百零])(?:第)?([0-9]{1,3}|[一二三四五六七八九十百零]{1,6})(中学|小学|中|小)(?:[（(][^()（）]{1,30}校区[)）])?$", value)
    if not match:
        return None
    number = match[1]
    if number.isdigit():
        if not 0 < int(number) < 1000:
            return None
        number = _school_number_text(int(number))
    return number, "中学" if match[2].startswith("中") else "小学"


def _metro_identity(value: str) -> tuple[str, str, frozenset[str]] | None:
    """Compare station spelling without guessing a city's station or exit."""
    value = re.sub(r"\s+", "", value)
    if not re.search(r"地铁|轻轨|轨道交通|站(?:[（(]?(?:[A-Za-z]\d*|\d+号?)?(?:出入口|出口|入口|口)[)）]?)?$", value):
        return None
    access = ""
    match = re.search(r"[（(]?(?P<id>[A-Za-z]\d*|\d+号?)?(?P<kind>出入口|出口|入口|口)[)）]?$", value)
    if match:
        access = (match.group("id") or match.group("kind")).upper().removesuffix("号")
        value = value[:match.start()]
    lines = frozenset(re.findall(r"[0-9一二三四五六七八九十]+号线|[sS]\d+线", value))
    value = re.sub(r"(?:地铁|轻轨|轨道交通)?(?:[0-9一二三四五六七八九十]+号线|[sS]\d+线)", "", value)
    value = re.sub(r"[（(](?:地铁站?|轻轨站?|轨道交通站?)[)）]", "", value)
    value = value.strip("()（）")
    value = re.sub(r"^(?:地铁|轻轨|轨道交通)", "", value)
    value = re.sub(r"(?:地铁站?|轻轨站?|轨道交通站?|站)$", "", value)
    if len(value) < 2 or re.search(r"[()（）]|地铁|轻轨|轨道交通", value):
        return None
    return value, access, lines


def _matches_landmark(point: GeoPoint, address: str, city: str, landmark: str, adcode: str = "") -> bool:
    name = point.name or _landmark(point.label.split(" · ", 1)[0], point.city)
    compact = lambda value: re.sub(r"[\s()（）·\-]", "", value)
    name, target = compact(name), compact(landmark)
    # 学校简称只做可解释的“第十二中学 -> 十二中”转换；不做模糊猜测。
    def school(value):
        value = re.sub(r"^第(?=[0-9一二三四五六七八九十百]+(?:中学|小学|中|小)$)", "", value)
        return re.sub(r"(中学|小学)$", lambda m: "中" if m.group() == "中学" else "小", value)
    name, target = school(name), school(target)
    city_name = point.city.removesuffix("市")
    if city_name and name.startswith(city_name):
        name = school(name[len(city_name):].removeprefix("市"))
    # Use un-compacted POI names so parentheses such as 吴山(地铁站) retain
    # their meaning. A requested A exit must never match B or a parent station.
    station_target = _metro_identity(landmark)
    station_name = _metro_identity(point.name or _landmark(point.label.split(" · ", 1)[0], point.city))
    if station_target and station_name:
        target_station, target_exit, target_lines = station_target
        returned_station, returned_exit, returned_lines = station_name
        returned_station = returned_station.removeprefix(point.city).removeprefix(city_name) if city_name else returned_station
        if (target_station != returned_station or target_exit and target_exit != returned_exit
                or target_lines and returned_lines and target_lines.isdisjoint(returned_lines)):
            return False
        return _same_city(point, address, city) and _district_matches(point, address, city, adcode)
    # 允许品牌前缀和同一地标的出入口，不接受“目标小区售楼部/附近商铺”。
    base = re.sub(r"(?:[东西南北]{1,2}门|出入口|入口|出口)$", "", name)
    if name == target and (re.search(r"地铁|轻轨|轨道交通|公交站", target)
                           or re.search(r"站(?:[A-Za-z]\d*|\d+号?)?(?:出入口|出口|入口|口)$", target)):
        base = name  # A provider may resolve the exact named transit entrance.
    if re.search(r"肯德基|麦当劳", landmark) and name.startswith(target):
        base = target  # 连锁分店还需通过下面的原文道路核验。
    prefix = target[:-len(base)] if base and target.endswith(base) else ""
    district_name = (point.district or split_address(point.label, point.city).district).rstrip("区县")
    explained_prefix = (bool(re.fullmatch(r"华润(?:置地)?|保利|泰禾|阳光", prefix))
                        or bool(prefix and prefix.endswith(("路", "街", "巷", "广场", "街道", "镇", "乡")))
                        or bool(prefix and district_name == prefix))
    if len(base) < 2 or not (base == target or len(base) >= 3 and explained_prefix):
        return False
    if not _same_city(point, address, city) or not _district_matches(point, address, city, adcode):
        return False
    if re.search(r"肯德基|麦当劳", landmark):
        roads = re.findall(r"[^省市区县镇乡街道，,;；\s]{2,12}(?:路|街|巷)", address)
        if not roads or not any(road in point.label for road in roads):
            return False
    return True


def distance_km(origin: GeoPoint, destination: GeoPoint) -> float:
    """GCJ-02 坐标间球面近似直线距离；不产生网络请求。"""
    # 再验证可防止 model_construct / model_copy 绕过有效性验证。
    origin = GeoPoint.model_validate(origin.model_dump())
    destination = GeoPoint.model_validate(destination.model_dump())
    latitude_1, latitude_2 = math.radians(origin.lat), math.radians(destination.lat)
    delta_latitude = latitude_2 - latitude_1
    delta_longitude = math.radians(destination.lng - origin.lng)
    haversine = math.sin(delta_latitude / 2) ** 2 + math.cos(latitude_1) * math.cos(latitude_2) * math.sin(delta_longitude / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))


_AREA_LEVELS = {"市", "区县", "乡镇", "村庄", "热点商圈", "开发区", "道路", "街道", "社区"}
_AREA_PART = re.compile(r"[^，,；;。\s()（）]{2,40}?(?:街道|大道|片区|商圈|园区|校区|社区|省|市|区|县|镇|乡|村|路|街|巷)")


def area_queries(address: str) -> list[str]:
    """只缩短原文中明确的地名，不用默认城市给无地址订单构造落点。"""
    nearby = _NEARBY.search(address)
    value = (nearby.group("near_prefix") or address[:nearby.start()]) if nearby else address
    value = value.strip()
    prefixes = [value[:match.end()].strip() for match in _AREA_PART.finditer(value)]
    # 国家、省、市域太宽；只有原文明确写到城市时才允许市级参考点。
    return list(dict.fromkeys(query for query in reversed(prefixes)
                             if not query.endswith("省") and not query.endswith("小区")))


def _area_context(point: GeoPoint, address: str, city: str) -> bool:
    if not _same_city(point, address, city):
        return False
    label_city = re.search(r"(?:^|[ ·])(?:[\u4e00-\u9fff]+省)?([\u4e00-\u9fff]{2,8}?市)", point.label)
    if label_city:
        returned = label_city.group(1).removesuffix("市")
        hint = city.removesuffix("市")
        if hint and returned not in hint and hint not in returned and returned not in address:
            return False
    return _district_matches(point, address, city)


def _area_scale(level: str = "", *, landmark: bool = False, query: str = "") -> Literal["small", "medium", "large"]:
    """按来源分档；回退到街道及以上时，保守采用较大范围。"""
    if landmark:
        return "small"
    if query.endswith(("街道", "乡", "镇", "区", "县", "市")) and not query.endswith(("社区", "园区", "校区")):
        return "large"
    scales = {"道路": "medium", "社区": "medium", "村庄": "medium", "热点商圈": "medium",
              "商圈": "medium", "街道": "large", "乡镇": "large", "区县": "large", "市": "large", "开发区": "large"}
    if level:
        return scales.get(level, "large")
    return "medium" if query.endswith(("路", "街", "巷", "社区", "村", "商圈")) else "large"


def _area_point(point: GeoPoint, label: str = "", *, level: str = "", landmark: bool = False, query: str = "") -> GeoPoint:
    return point.model_copy(update={"precision": "area", "label": label or point.label,
                                    "area_scale": _area_scale(level, landmark=landmark, query=query)})


def _anchor_in_label(query: str, label: str, city: str) -> bool:
    variants = (query, query.removeprefix(city), query.removeprefix(city.removesuffix("市")))
    return any(value and value in label for value in variants)


def _region_from_geocodes(records: list[tuple[GeoPoint, str]], address: str, city: str, *, fallback: bool = False) -> GeoPoint | None:
    anchors = area_queries(address)
    valid = [(point, level) for point, level in records
             if level in _AREA_LEVELS and _area_context(point, address, city)
             and any(_anchor_in_label(query, point.label, city) for query in anchors)]
    # 同一查询返回多个分散区域，不能选第一个区域当成唯一落点。
    if len(valid) == 1:
        return _area_point(valid[0][0], level=valid[0][1], query=address if fallback else "")
    return None


def _cluster_area(points: list[GeoPoint], address: str, city: str) -> GeoPoint | None:
    landmark = _landmark(address)
    anchors = area_queries(address)
    relevant = [point for point in points if _area_context(point, address, city)
                and (_matches_landmark(point, address, city, landmark) if landmark
                     else any(_anchor_in_label(query, point.label, city) for query in anchors))]
    if not relevant:
        return None
    # 单一搜索结果也可作地标参考点；多个出口/同名候选只有聚集时才合并。
    if any(distance_km(first, second) > 1.5 for first in relevant for second in relevant):
        return None
    label = landmark or next((query for query in anchors if all(_anchor_in_label(query, point.label, city) for point in relevant)), "")
    if not label:
        return None
    return GeoPoint(lng=(min(p.lng for p in relevant) + max(p.lng for p in relevant)) / 2,
                    lat=(min(p.lat for p in relevant) + max(p.lat for p in relevant)) / 2,
                    city=relevant[0].city, label=label, precision="area",
                    area_scale=_area_scale(landmark=bool(landmark), query=label))


class AmapClient:
    """固定官方 HTTPS 端点，无重试、无重定向、无隐式备用供应商。"""

    def __init__(self, web_key: str, transport: httpx.BaseTransport | None = None):
        if not isinstance(web_key, str) or not web_key.strip() or len(web_key) > 200:
            raise MapAPIError("请先配置高德 Web 服务 Key。")
        self._key = web_key.strip()
        self._transport = transport
        self._region_geocodes: dict[tuple[str, str], list] = {}

    def _pace_request(self) -> None:
        """串行限制真实 REST 请求；同一地址内的 POI/区域补查也要限速。"""
        global _NEXT_REQUEST_AT
        if self._transport is not None:
            return  # Explicit transports are used by offline callers/tests.
        with _REQUEST_LOCK:
            delay = _NEXT_REQUEST_AT - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            _NEXT_REQUEST_AT = time.monotonic() + _REQUEST_INTERVAL

    def _request(self, endpoint: str, params: dict) -> dict:
        if endpoint not in _PATHS:
            raise MapAPIError("地图接口不可用。")
        try:
            self._pace_request()
            with httpx.Client(transport=self._transport, timeout=15.0, follow_redirects=False) as client:
                response = client.get("https://restapi.amap.com" + _PATHS[endpoint], params={**params, "key": self._key})
                if response.status_code == 429:
                    raise MapAPIError("高德额度或请求频率达到限制，已暂停后续查询，请检查账号权益后手动重试。", quota_exhausted=True, code="429", endpoint=endpoint)
                response.raise_for_status()
                data = response.json()
        except MapAPIError:
            raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise MapAPIError("地图服务请求失败，请稍后手动重试。", stop_batch=status not in {400, 422, 500, 502, 503, 504}, code=str(status), endpoint=endpoint) from None
        except (httpx.HTTPError, ValueError, TypeError):
            raise MapAPIError("地图服务连接或响应失败，请检查网络后手动重试。", endpoint=endpoint) from None
        if not isinstance(data, dict):
            raise MapAPIError("地图服务返回格式无效，请稍后手动重试。", endpoint=endpoint)
        if endpoint == "骑行" and "errcode" in data:
            code = str(data.get("errcode"))
            success = code == "0"
        else:
            code = str(data.get("infocode", ""))
            success = str(data.get("status")) == "1" and code in ("", "10000")
        if success:
            return data
        # errmsg 只用于类别判断，永不展示服务端错误文本。
        message = str(data.get("info", data.get("errmsg", ""))).upper()
        if code in _LIMIT_CODES or any(token in message for token in ("OVER_LIMIT", "EXCEEDED", "RUN_OUT", "TOO_FREQUENT")):
            raise MapAPIError("高德额度或请求频率达到限制，已暂停后续查询，请检查账号权益后手动重试。", quota_exhausted=True, code=code, endpoint=endpoint)
        if code in _AUTH_CODES:
            raise MapAPIError("高德凭据、访问限制或接口权限不可用，请检查控制台配置。", code=code, endpoint=endpoint)
        if code in {"20800", "20801", "20802", "20803"}:
            raise MapAPIError("当前起终点未找到可用路线，请确认位置或改用其他出行方式。")
        if code == "10016":
            raise MapAPIError("高德服务繁忙，此地址暂未完成定位，可稍后手动重试。", stop_batch=False, code=code, endpoint=endpoint)
        raise MapAPIError("此地址查询未成功，可检查地点后手动重试。", stop_batch=not bool(re.fullmatch(r"\d{1,6}", code)), code=code, endpoint=endpoint)

    def _district_scope(self, city: str, district: str) -> str:
        """最多补查一次明确区县；只使用城市、区县均核对一致的在线编码。"""
        query = district  # city 参数提供城市上下文，查询不包含订单地名或范围。
        key = (query, city)
        if key not in self._region_geocodes:
            params = {"address": query}
            if city:
                params["city"] = clean_query(city, city=True)
            codes = self._request("geocode", params).get("geocodes", [])
            if not isinstance(codes, list):
                raise MapAPIError("地图定位结果格式无效，请稍后手动重试。")
            self._region_geocodes[key] = codes
        points = [_geocode_point(item) for item in self._region_geocodes[key][:25] if isinstance(item, dict)]
        valid = {p.adcode for p in points if p and re.fullmatch(r"\d{6}", p.adcode)
                 and not p.adcode.endswith("00") and _same_city(p, district, city)
                 and _district_matches(p, district, city)}
        return next(iter(valid)) if len(valid) == 1 else ""

    def search(self, query: str, city: str = "", *, district: str = "", adcode: str = "",
               citylimit: bool | None = None) -> list[GeoPoint]:
        cleaned = clean_query(query)
        parts = split_address(cleaned, city)
        city = parts.city or city
        district = district or parts.district
        adcode = adcode if re.fullmatch(r"\d{6}", adcode) else ""
        limited = (parts.city_explicit or bool(district) or bool(adcode)) if citylimit is None else citylimit
        if district and not adcode:
            adcode = self._district_scope(city, district)
        params = {"keywords": parts.landmark or cleaned, "offset": "10", "page": "1",
                  "extensions": "all", "citylimit": "true" if limited else "false"}
        scope = adcode or city
        if scope.strip():
            params["city"] = clean_query(scope, city=True)
        data = self._request("search", params)
        pois = data.get("pois", [])
        if not isinstance(pois, list):
            raise MapAPIError("地图地点结果格式无效，请稍后手动重试。")
        points = []
        for place in pois[:25]:
            if not isinstance(place, dict):
                continue
            name = _text(place.get("name"))
            place_district = _text(place.get("adname"))
            address = _text(place.get("address"))
            place_city = _text(place.get("cityname"))
            label = " · ".join(part for part in (name, place_city + place_district + address) if part)
            point = _point(place.get("location"), label=label, city=place_city, district=place_district,
                           adcode=_text(place.get("adcode")), poi_id=_text(place.get("id")), name=name,
                           city_code=_text(place.get("citycode")))
            if point is not None:
                points.append(point)
        return _distinct(points)

    def locate(self, address: str, city: str = "") -> GeoResult:
        original = clean_query(address)
        parts = split_address(original, city)
        landmark = _landmark(original, city)
        if (landmark and county_city_parent(parts.district)
                and county_city_parent(parts.district).removesuffix("市") == parts.city.removesuffix("市")):
            return self._locate_county_reference(original, parts.city, parts.district, landmark)
        # A numbered school in a "nearby" description can anchor a reference
        # area after a bounded, city-scoped search, never an exact lesson venue.
        school = _numbered_school(_landmark(original, city)) if parts.relative and parts.city else None
        # Only simplify a terminal transit access description. Keep ordinary
        # building entrances and the order's original address untouched.
        station = ""
        if (re.search(r"地铁|轻轨|轨道交通|公交站", original)
                or re.search(r"站\s*[(（]?\s*(?:[A-Za-z]\d*|\d+号?)?\s*(?:出入口|出口|入口|口)\s*[)）]?\s*$", original)):
            station = re.sub(r"\s*[(（]?\s*(?:(?:[A-Za-z]\d*|\d+号?)\s*(?:出入口|出口|入口|口)|出入口|出口|入口)\s*[)）]?\s*$", "", original).strip()
            if station == original or not re.search(r"(?:地铁|轻轨|站|站点)$", station):
                station = ""
        try:
            result = self._locate_address(original, city)
        except MapAPIError as exc:
            if not (station or school) or exc.code != "30001" or exc.endpoint != "geocode":
                raise
            result = None
        if station and (result is None or (result.status in {"not_found", "ambiguous"} and result.area_point is None)):
            fallback = self._locate_address(station, city)
            reference = fallback.area_point
            if reference is None and fallback.status == "located" and len(fallback.candidates) == 1:
                reference = _area_point(fallback.candidates[0], landmark=True)
            if reference is not None:
                return fallback.model_copy(update={
                    "status": "ambiguous", "area_point": reference,
                    "message": "已按站点显示参考位置，尚未确认具体入口或出口；距离仅供参考。",
                })
            return result if result is not None else fallback
        if school and (result is None or result.status in {"not_found", "ambiguous"} and result.area_point is None):
            number, kind = school
            candidates = self.search(f"第{number}{kind}", parts.city, district=parts.district, citylimit=True)
            matched = [point for point in candidates
                       if _numbered_school(point.name or point.label.split(" · ", 1)[0]) == school
                       and _same_city(point, original, parts.city)
                       and _district_matches(point, original, parts.city)]
            if len(matched) == 1:
                point = matched[0]
                return GeoResult(status="ambiguous", candidates=matched,
                                 area_point=_area_point(point, point.name or point.label, landmark=True),
                                 message="已按核验学校显示附近参考范围，具体上课地点待确认。",
                                 resolver_revision=LOCATION_REVISION)
            if result is None:
                return GeoResult(status="ambiguous" if matched else "not_found", candidates=matched,
                                 message="学校候选尚不能唯一确认，暂不显示参考范围。", resolver_revision=LOCATION_REVISION)
        return result

    def _locate_county_reference(self, address: str, city: str, district: str, landmark: str) -> GeoResult:
        # Resolve the county code independently before accepting a POI. The
        # provider's cityname may name the prefecture while adname is a city.
        adcode = self._district_scope(city, district)
        candidates = self.search(landmark, city, district=district, adcode=adcode, citylimit=True)
        matched = [point for point in candidates if adcode and point.adcode == adcode and point.district == district
                   and _matches_landmark(point, address, city, landmark, adcode)]
        if len(matched) == 1:
            point = matched[0]
            return GeoResult(status="ambiguous", candidates=matched,
                             area_point=_area_point(point, landmark, landmark=True),
                             message="已按核验小区或地标显示参考位置，具体上课地点待确认。",
                             resolver_revision=LOCATION_REVISION)
        return GeoResult(status="ambiguous" if candidates else "not_found", candidates=candidates,
                         message="县级市或地点信息尚不能唯一核验，暂不显示参考位置。",
                         resolver_revision=LOCATION_REVISION)

    def _locate_address(self, address: str, city: str = "") -> GeoResult:
        original_address = clean_query(address)
        parts = split_address(original_address, city)
        query = parts.query
        city = parts.city or city
        if not query:
            return GeoResult(status="not_found", message="缺少可检索的地名，请确认原始地址。", resolver_revision=LOCATION_REVISION)
        params = {"address": query}
        if city.strip():
            params["city"] = clean_query(city, city=True)
        data = self._request("geocode", params)
        geocodes = data.get("geocodes", [])
        if not isinstance(geocodes, list):
            raise MapAPIError("地图定位结果格式无效，请稍后手动重试。")
        points = []
        records = []
        coarse = False
        for item in geocodes[:25]:
            if not isinstance(item, dict):
                continue
            level = _text(item.get("level"))
            coarse = coarse or level in _COARSE_LEVELS or not level
            point = _geocode_point(item)
            if point is not None:
                points.append(point)
                records.append((point, level))
        points = _distinct(points)
        # 地址解析也必须核对行政区和地名，不能直接信任单条高精度返回。
        landmark = _landmark(original_address, city)
        wrong_context = any(not _same_city(p, params["address"], city)
                            or not _district_matches(p, params["address"], city)
                            or (landmark and not _matches_landmark(p, params["address"], city, landmark))
                            for p in points)
        # 默认城市是提示而非全国同名地点的确定依据。无明确外地城市时，
        # 单个异地返回也进入待确认，避免误把另一城市同名小区自动落点。
        city_hint = params.get("city", "").removesuffix("市")
        wrong_city = False
        if city_hint and re.search(r"[\u4e00-\u9fff]", city_hint):
            for candidate in points:
                returned_city = candidate.city.removesuffix("市")
                if returned_city and city_hint not in returned_city and returned_city not in city_hint and returned_city not in params["address"]:
                    wrong_city = True
        if not points or len(points) > 1 or coarse or wrong_city or wrong_context:
            search_points = []
            adcode = ""
            if landmark or not points:
                query = landmark or params["address"]
                codes = {p.adcode for p in points if p.adcode and not p.adcode.endswith("00")
                         and _same_city(p, params["address"], city) and _district_matches(p, params["address"], city)}
                adcode = next(iter(codes)) if parts.district and len(codes) == 1 else ""
                candidates = self.search(query, city, district=parts.district, adcode=adcode,
                                         citylimit=parts.city_explicit or bool(parts.district))
                # search 的补查编码只约束同一区县；无编码时仍保留城市及区县核验。
                if parts.district and not adcode:
                    adcode = self._district_scope(city, parts.district)
                search_points = candidates
                matched = [p for p in candidates if landmark and _matches_landmark(p, params["address"], city, landmark, adcode)]
                if len(matched) == 1:
                    return GeoResult(status="located", candidates=matched,
                                     area_point=_area_point(matched[0], landmark, landmark=True) if parts.relative else None,
                                     message="按匹配的小区或地标显示约略位置，距离仅供参考。", resolver_revision=LOCATION_REVISION)
                points = _distinct(candidates + points)[:25]
            area_records = [(p, level) for p, level in records if _district_matches(p, params["address"], city, adcode)]
            known_points = [point for point, level in area_records if level and level not in {"国家", "省", "未知"}]
            search_points = [p for p in search_points if _district_matches(p, params["address"], city, adcode)]
            area = _region_from_geocodes(area_records, params["address"], city) or _cluster_area(_distinct(search_points + known_points), params["address"], city)
            if area is None:
                # 道路、街道、区县逐级退回，最多补查3个原文区域；缓存后不重复请求。
                queries = [query for query in area_queries(params["address"]) if query != params["address"]][:3]
                for query in queries:
                    region = split_address(query, city)
                    cache_key = (region.district, city) if region.district and not region.locality else (query, city)
                    cached = self._region_geocodes.get(cache_key)
                    fallback_codes = cached if cached is not None else self._request("geocode", {**params, "address": query}).get("geocodes", [])
                    if not isinstance(fallback_codes, list):
                        raise MapAPIError("地图定位结果格式无效，请稍后手动重试。")
                    regions = []
                    for item in fallback_codes[:25]:
                        if not isinstance(item, dict):
                            continue
                        point = _geocode_point(item)
                        if point and _area_context(point, query, city):
                            regions.append((point, _text(item.get("level"))))
                    area = _region_from_geocodes(regions, query, city, fallback=True)
                    if area:
                        break
            if area:
                return GeoResult(status="ambiguous", candidates=points, area_point=area,
                                 message="已显示区域参考位置，具体上课地点待确认；参考距离不用于半径排除。", resolver_revision=LOCATION_REVISION)
            if not points:
                return GeoResult(status="not_found", message="未找到可靠地点，订单仍保留在列表。", resolver_revision=LOCATION_REVISION)
            return GeoResult(status="ambiguous", candidates=points, message="候选地点分散或缺少可核对区域，暂不显示地图落点。", resolver_revision=LOCATION_REVISION)
        if parts.relative:
            return GeoResult(status="located", candidates=points, area_point=_area_point(points[0], landmark=True),
                             message="已标记附近地标的参考位置，具体上课地点待确认。", resolver_revision=LOCATION_REVISION)
        return GeoResult(status="located", candidates=points, message="约略位置，仅供附近筛选参考。" if points[0].precision == "approximate" else "地址定位成功；直线距离仅供参考。", resolver_revision=LOCATION_REVISION)

    def commute(self, origin: GeoPoint, dest: GeoPoint, mode: str, city: str = "") -> CommuteResult:
        if mode not in MODES:
            raise MapAPIError("当前服务商暂不支持该交通方式。")
        # 经纬度验证后才拼装请求；不发送地点原文或标签。
        origin = GeoPoint.model_validate(origin.model_dump())
        dest = GeoPoint.model_validate(dest.model_dump())
        params = {"origin": f"{origin.lng:.6f},{origin.lat:.6f}", "destination": f"{dest.lng:.6f},{dest.lat:.6f}"}
        if mode == "公共交通":
            origin_city = origin.city or city
            dest_city = dest.city or city or origin_city
            if not origin_city:
                raise MapAPIError("查询公共交通前，请设置出发点所在城市。")
            params.update(city=clean_query(origin_city, city=True), cityd=clean_query(dest_city, city=True), extensions="base", strategy="0")
        elif mode != "骑行":
            params["extensions"] = "base"
        data = self._request(mode, params)
        route = data.get("data" if mode == "骑行" else "route", {})
        if not isinstance(route, dict):
            raise MapAPIError("地图路线结果格式无效，请稍后手动重试。")
        paths = route.get("transits" if mode == "公共交通" else "paths", [])
        valid = []
        if isinstance(paths, list):
            for path in paths:
                if not isinstance(path, dict):
                    continue
                try:
                    seconds, meters = float(path["duration"]), float(path["distance"])
                    result = CommuteResult(mode=mode, minutes=seconds / 60, distance_m=meters, summary="高德返回方案中用时最短的可用方案")
                    valid.append(result)
                except (KeyError, TypeError, ValueError, ValidationError):
                    continue
        if not valid:
            raise MapAPIError("当前起终点未找到可用路线，可确认位置或打开外部地图查看。")
        return min(valid, key=lambda result: result.minutes)
