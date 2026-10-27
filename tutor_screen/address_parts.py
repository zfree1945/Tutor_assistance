"""可缺省的地址结构；原文与检索词分离，不根据方位距离推算坐标。"""
from dataclasses import dataclass
import re

from .address_rules import DIRECTION, DISTANCE, NEARBY, address_query


# 无“市”的城市和无“区/县”的简称只用明确词表识别，不任意给地名加后缀。
# 其他城市仍支持完整行政区写法以及调用方提供的城市提示。
_DISTRICTS = {
    "福州": ("鼓楼区", "台江区", "仓山区", "马尾区", "晋安区", "长乐区",
           "闽侯县", "连江县", "罗源县", "闽清县", "永泰县", "平潭县", "福清市"),
    "温州": ("鹿城区", "龙湾区", "瓯海区", "洞头区", "永嘉县", "平阳县", "苍南县", "文成县", "泰顺县", "瑞安市", "乐清市", "龙港市"),
}
_CITIES = ("福州", "温州", "杭州", "宁波", "厦门", "泉州", "漳州", "莆田", "广州", "深圳", "北京", "上海", "天津", "重庆")
_PROVINCE = re.compile(r"^(.{2,8}?(?:省|自治区))")
_CITY = re.compile(r"^([\u4e00-\u9fff]{2,8}?市)")
_DISTRICT = re.compile(r"^([\u4e00-\u9fff]{2,6}?[区县])")
_SEPARATORS = " ，,；;()（）"


def county_city_parent(name: str) -> str:
    """Use the existing explicit hierarchy; never infer a parent by spelling."""
    return next((parent + "市" for parent, districts in _DISTRICTS.items()
                 if name.endswith("市") and name in districts), "")


@dataclass(frozen=True)
class AddressParts:
    original: str
    province: str = ""
    city: str = ""
    district: str = ""
    locality: str = ""
    landmark: str = ""
    relative: str = ""
    directions: tuple[str, ...] = ()
    distances: tuple[str, ...] = ()
    query: str = ""
    city_explicit: bool = False


def split_address(address: str, city_hint: str = "") -> AddressParts:
    """缺少任何行政层级也继续检索；未知简称保留为地名，不猜测所属区县。"""
    query = address_query(address).strip(_SEPARATORS)
    value = query
    province = ""
    if match := _PROVINCE.match(value):
        province = match.group(1)
        value = value[match.end():].lstrip(_SEPARATORS)
    city = ""
    explicit = False
    # 先匹配明确城市，防止“福州仓山区”被整体吞成一个区名。
    city_names = sorted(set((*_CITIES, city_hint.removesuffix("市"))) - {""}, key=len, reverse=True)
    for name in city_names:
        if value.startswith(name):
            city = name + "市"
            value = value[len(name):].removeprefix("市").lstrip(_SEPARATORS)
            explicit = True
            break
    if not city and not value.endswith("超市") and (match := _CITY.match(value)):
        city = match.group(1)
        value = value[match.end():].lstrip(_SEPARATORS)
        explicit = True
    city = city or city_hint.strip()
    district = ""
    if parent := county_city_parent(city):
        district, city = city, parent
    for name in sorted(_DISTRICTS.get(city.removesuffix("市"), ()), key=len, reverse=True):
        if value.startswith(name):
            district, value = name, value[len(name):]
            break
        # 有城市上下文才接受省略区县后缀；县级市简称不自动扩展。
        alias = name[:-1]
        if city.removesuffix("市") == "福州" and not name.endswith("市") and value.startswith(alias):
            district, value = name, value[len(alias):]
            break
    if not district and (match := _DISTRICT.match(value)):
        name = match.group(1)
        if not name.endswith(("小区", "社区", "校区", "园区", "片区")):
            district, value = name, value[match.end():]
    value = value.strip(_SEPARATORS)
    local_nearby = NEARBY.search(value)
    local_relation = ""
    if local_nearby and local_nearby.group("near_prefix") is not None:
        local_relation = local_nearby.group()[len(local_nearby.group("near_prefix")):]
        value = address_query(value)
    # local query 保留街道、门牌等信息；landmark 单独供 POI 检索核验。
    nearby = NEARBY.search(address)
    anchor = value
    if nearby and nearby.group("near_prefix"):
        anchor = split_address(address_query(address[nearby.end():]), city).locality
    anchor = re.sub(r"^.{2,12}?(?:街道|镇|乡)", "", anchor)
    landmark = re.sub(r"[（(].*?[）)]", "", anchor)
    landmark = re.sub(r"\d+(?:号楼|栋|幢|单元|室).*$", "", landmark)
    landmark = re.split(r"街道|大道|路|街|巷", landmark)[-1].strip()
    if re.fullmatch(r".*(?:镇|乡|村)|\d+号|待定|未知|不详", landmark):
        landmark = ""
    relative = ""
    if nearby:
        prefix = nearby.group("near_prefix")
        start = nearby.start() + len(prefix or "") if prefix is not None else nearby.start()
        # 前置关系词只保留关系本身；后置范围完整保留，便于展示与复核。
        relative = address[start:nearby.end()] if prefix is not None else address[start:].strip(_SEPARATORS)
        if prefix is not None and (suffix := NEARBY.search(address[nearby.end():])):
            relative += " " + address[nearby.end() + suffix.start():].strip(_SEPARATORS)
    if local_relation:
        relative = (local_relation + " " + relative).strip()
    normalized = province + (city if explicit else "") + district + value
    return AddressParts(address, province, city, district, value, landmark, relative,
                        tuple(m.group() for m in re.finditer(DIRECTION, relative)),
                        tuple(m.group().strip() for m in re.finditer(DISTANCE, relative, re.IGNORECASE)),
                        normalized, explicit)
