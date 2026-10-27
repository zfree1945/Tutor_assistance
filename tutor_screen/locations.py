"""从明确字段和订单复合标题读取原文地点；提取与脱敏共用字符范围。"""
import re
from dataclasses import dataclass

from .text_fields import CLOSE_BRACKET, OPEN_BRACKET, address_value_bounds, read_fields

GRADE = r"(?:小学|初中|高中|初[一二三1-3]|高[一二三1-3]|小[一二三四五六1-6])"
SUBJECT = r"语文|数学|英语|英文|科学|物理|化学|生物|历史|政治|地理|技术|全科|理科|文科|数理化|日语"
TITLE = re.compile(rf"{OPEN_BRACKET}(?P<content>{GRADE}[^】\]〗〕］」\r\n]{{1,100}}){CLOSE_BRACKET}")
PLACE_HINT = re.compile(r"区|市|县|镇|乡|路|街|道|巷|村|小区|大厦|广场|花园|公寓|组团|苑|楼|湾|万达|校区|鹿城")
NOT_LOCATION = re.compile(r"^(?:线上|网课|远程|待定|未知|不详|无|未提供|可协商)$|基础|成绩|不及格|要求|周[一二三四五六日天]|每周")


@dataclass
class LocationSpan:
    value: str
    start: int
    end: int
    source: str
    explicit: bool = False


@dataclass
class OrderTitle:
    start: int
    end: int
    summary: str
    source: str
    location: LocationSpan | None


def read_titles(text: str) -> list[OrderTitle]:
    titles = []
    for match in TITLE.finditer(text):
        content = match.group("content")
        parts = re.split(r"[，,；;|]", content, maxsplit=1)
        summary = parts[0].strip()
        if not re.search(SUBJECT, summary):
            continue
        location = None
        if len(parts) == 2:
            value = parts[1].strip()
            if value and PLACE_HINT.search(value) and not NOT_LOCATION.search(value):
                start = match.start("content") + len(parts[0]) + 1 + len(parts[1]) - len(parts[1].lstrip())
                location = LocationSpan(value, start, start + len(value), match.group())
        titles.append(OrderTitle(match.start(), match.end(), summary, match.group(), location))
    return titles


def location_spans(text: str, *, for_redaction: bool = False) -> list[LocationSpan]:
    explicit = [LocationSpan(field.value, field.value_start, field.value_end, field.source, True)
                for field in read_fields(text) if field.category == "address" and field.value and not NOT_LOCATION.search(field.value)]
    # 仅兜底独立的“地址/地点+地名”行；不放宽通用字段规则。
    fallback = []
    for match in re.finditer(r"(?:^|(?<=[\r\n]))[ \t]*(?:地址|地点)[ \t\u3000]*(?P<place>[\u4e00-\u9fff][ \t\u3000]*[\u4e00-\u9fff][^\r\n]*)", text):
        start, end = address_value_bounds(text, match.start("place"), match.end("place"))
        value = text[start:end]
        compact = re.sub(r"[ \t\u3000]+", "", value)
        if (PLACE_HINT.search(compact) and not NOT_LOCATION.search(compact)
                and not any(span.start < end and start < span.end for span in explicit)):
            fallback.append(LocationSpan(value, start, end, text[match.start():end].strip(), True))
    titles = read_titles(text)
    inline = []
    # 常见短单：编号【初中全科瓯海区】梧田街道某小区3栋 初3男生，...
    # 只取标题后、下一个学员/年级字段前的明确地点，不吞入教学要求。
    for title in titles:
        match = re.match(
            r"\s*(?P<place>[^\r\n，,；;]{2,100}?)\s*(?=(?:[小初高][一二三四五六1-6]|[一二三四五六七八九1-9]年级|学员|学生|男生|女生))",
            text[title.end:],
        )
        if match:
            value = match.group("place").strip()
            if PLACE_HINT.search(value) and not NOT_LOCATION.search(value):
                start = title.end + match.start("place")
                inline.append(LocationSpan(value, start, start + len(value), text[title.start:start + len(value)]))
    spans = explicit + fallback + [title.location for title in titles if title.location] + inline
    if for_redaction:
        spans.extend(_unstructured_location_spans(text, spans))
    return spans


def _unstructured_location_spans(text: str, known: list[LocationSpan]) -> list[LocationSpan]:
    """未标注地点仅用于隐私保护，不作为订单地址或定位依据。"""
    patterns = [
        r"(?:地址|地点|位置|上课地点|小区|住址)[ \t]*[:：]?[ \t]*[^；;。\s█】\]〗〕］」]{2,100}",
        r"[\u4e00-\u9fffA-Za-z0-9]{2,40}(?:路|街|道|巷|小区|大厦|广场|校区|花园|公寓)[^，,；;。\s█】\]〗〕］」]{0,60}",
    ]
    spans = []
    offset = 0
    for line in text.splitlines(keepends=True):
        fields = read_fields(line)
        for pattern in patterns:
            for match in re.finditer(pattern, line):
                start, end = offset + match.start(), offset + match.end()
                if any(span.start < end and start < span.end for span in known):
                    continue
                # “要求思路清晰”等教学描述不能被“路/道”误判为地址。
                # 教学字段中的地点需有明确住址提示或门牌、楼栋证据。
                in_business_field = any(field.category != "address" and field.value_start <= match.start() < field.value_end for field in fields)
                value = match.group()
                if in_business_field and not re.search(r"地址|地点|住址|家住|住在|位于|\d+(?:号|栋|幢|单元|室)", value):
                    continue
                spans.append(LocationSpan(value, start, end, line.strip()))
        offset += len(line)
    return spans


def mask_locations(text: str, replacement: str = " ", *, for_redaction: bool = False) -> str:
    """按原文坐标等长遮盖；重叠或重复范围不影响后续字符位置。"""
    chars = list(text)
    for location in location_spans(text, for_redaction=for_redaction):
        chars[location.start:location.end] = [replacement if ch not in "\r\n" else ch for ch in chars[location.start:location.end]]
    return "".join(chars)


def without_locations(text: str) -> str:
    return mask_locations(text)
