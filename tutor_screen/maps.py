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


MODES = ("公共交通", "骑行", "步行", "驾车")
LOCATION_REVISION = 3
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


def _point(location, *, label: str, city: str, precision: str = "approximate") -> GeoPoint | None:
    if not isinstance(location, str):
        return None
    try:
        longitude, latitude = location.split(",")
        return GeoPoint(lng=float(longitude), lat=float(latitude), label=label[:300], city=city[:100], precision=precision)
    except (ValueError, TypeError, ValidationError):
        return None


def _distinct(points: list[GeoPoint]) -> list[GeoPoint]:
    found: dict[tuple, GeoPoint] = {}
    for point in points:
        # 同名地点有多个出口时不合并；只删除同标签、同坐标的重复返回。
        found.setdefault((round(point.lng, 6), round(point.lat, 6), point.label), point)
    return list(found.values())


def _district_hint(address: str) -> str:
    match = re.match(r"^(?:.*?[省市])?([\u4e00-\u9fff]{2,6}?[区县])", address)
    value = match.group(1) if match else ""
    return "" if value.endswith(("小区", "社区", "校区", "园区")) else value


def _landmark(address: str) -> str:
    nearby = _NEARBY.search(address)
    value = address[nearby.end():] if nearby and nearby.group("near_prefix") else address
    value = _NEARBY.split(value, maxsplit=1)[0]
    value = re.sub(r"[（(].*?[）)]", "", value)
    value = re.sub(r"\d+(?:号楼|栋|幢|单元|室).*$", "", value)
    value = re.split(r"街道|路|街|巷", value)[-1].strip()
    value = re.sub(r"^(?:.*?[省市])?" + re.escape(_district_hint(value)), "", value) if _district_hint(value) else value
    # 单纯道路/行政区不作为能唯一识别的 POI。
    return value if len(value) >= 3 and re.search(r"园|苑|湾|城|世家|小区|公寓|大厦|广场|学校|中学|小学|[0-9一二三四五六七八九十百零〇两]+[中小]|大学|学院|校区|医院|商场|超市|体育馆|地铁|站|肯德基|麦当劳", value) else ""


def _same_city(point: GeoPoint, address: str, city: str) -> bool:
    hint, returned = city.removesuffix("市"), point.city.removesuffix("市")
    return bool(returned and (not hint or hint in returned or returned in hint or returned in address))


def _matches_landmark(point: GeoPoint, address: str, city: str, landmark: str) -> bool:
    name = point.label.split(" · ", 1)[0]
    compact = lambda value: re.sub(r"[\s()（）·\-]", "", value)
    name, target = compact(name), compact(landmark)
    if len(name) < 3 or not (target in name or target.endswith(name)):
        return False
    if not _same_city(point, address, city) or point.city.removesuffix("市") not in point.label:
        return False
    district = _district_hint(address)
    if district and district not in point.label:
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
# 单字“近”须有行政区/街道前缀；“近江”是专名，不能解释为附近。
_NEARBY = re.compile(
    r"附近|对面|旁边|周边|周围|旁|"
    r"(?P<near_prefix>^[\u4e00-\u9fff]{2,}?(?:街道|省|市|区|县|镇|乡|路|街|巷)[^近，,；;。()（）]*?)近(?!江)(?=.)"
)


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
    district = _district_hint(address)
    return not district or district in point.label


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

    def search(self, query: str, city: str = "") -> list[GeoPoint]:
        params = {"keywords": clean_query(query), "offset": "10", "page": "1", "extensions": "base", "citylimit": "false"}
        if city.strip():
            params["city"] = clean_query(city, city=True)
        data = self._request("search", params)
        pois = data.get("pois", [])
        if not isinstance(pois, list):
            raise MapAPIError("地图地点结果格式无效，请稍后手动重试。")
        points = []
        for place in pois[:25]:
            if not isinstance(place, dict):
                continue
            name = _text(place.get("name"))
            district = _text(place.get("adname"))
            address = _text(place.get("address"))
            place_city = _text(place.get("cityname"))
            label = " · ".join(part for part in (name, place_city + district + address) if part)
            point = _point(place.get("location"), label=label, city=place_city or city)
            if point is not None:
                points.append(point)
        return _distinct(points)

    def locate(self, address: str, city: str = "") -> GeoResult:
        params = {"address": clean_query(address)}
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
            precision = "exact" if level in {"门牌号", "单元号", "门址"} else "approximate"
            point = _point(item.get("location"), label=_text(item.get("formatted_address")), city=_text(item.get("city")) or _text(item.get("province")) or city, precision=precision)
            if point is not None:
                points.append(point)
                records.append((point, level))
        points = _distinct(points)
        # 默认城市是提示而非全国同名地点的确定依据。无明确外地城市时，
        # 单个异地返回也进入待确认，避免误把另一城市同名小区自动落点。
        city_hint = params.get("city", "").removesuffix("市")
        wrong_city = False
        if city_hint and re.search(r"[\u4e00-\u9fff]", city_hint):
            for candidate in points:
                returned_city = candidate.city.removesuffix("市")
                if returned_city and city_hint not in returned_city and returned_city not in city_hint and returned_city not in params["address"]:
                    wrong_city = True
        if not points or len(points) > 1 or coarse or wrong_city:
            search_points = []
            landmark = _landmark(params["address"])
            if landmark or not points:
                query = (_district_hint(params["address"]) + landmark) if landmark else params["address"]
                candidates = self.search(query, city)
                search_points = candidates
                matched = [p for p in candidates if landmark and _matches_landmark(p, params["address"], city, landmark)]
                if len(matched) == 1:
                    return GeoResult(status="located", candidates=matched,
                                     area_point=_area_point(matched[0], landmark, landmark=True) if _NEARBY.search(params["address"]) else None,
                                     message="按匹配的小区或地标显示约略位置，距离仅供参考。", resolver_revision=LOCATION_REVISION)
                points = _distinct(candidates + points)[:25]
            known_points = [point for point, level in records if level and level not in {"国家", "省", "未知"}]
            area = _region_from_geocodes(records, params["address"], city) or _cluster_area(_distinct(search_points + known_points), params["address"], city)
            if area is None:
                # 道路、街道、区县逐级退回，最多补查3个原文区域；缓存后不重复请求。
                queries = [query for query in area_queries(params["address"]) if query != params["address"]][:3]
                for query in queries:
                    fallback = self._request("geocode", {**params, "address": query})
                    fallback_codes = fallback.get("geocodes", [])
                    if not isinstance(fallback_codes, list):
                        raise MapAPIError("地图定位结果格式无效，请稍后手动重试。")
                    regions = []
                    for item in fallback_codes[:25]:
                        if not isinstance(item, dict):
                            continue
                        point = _point(item.get("location"), label=_text(item.get("formatted_address")),
                                       city=_text(item.get("city")) or _text(item.get("province")) or city)
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
        if _NEARBY.search(params["address"]):
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
