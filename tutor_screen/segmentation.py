"""逐块识别订单编号与粘连线索，模型只提供可验证的原文边界。"""

import re
from dataclasses import dataclass

from .models import OrderBoundary
from .text_fields import CLOSE_BRACKET, FIELD_NAMES, FIELD_SEPARATOR, OPEN_BRACKET, read_fields
from .locations import read_titles


@dataclass
class Segment:
    raw_text: str
    uncertain: bool = False


@dataclass
class OrderMarker:
    start: int
    end: int
    number: str
    is_start: bool = True


FIELD_PATTERNS = [
    re.compile(rf"(?:{OPEN_BRACKET}\s*(?:{names})\s*{CLOSE_BRACKET}\s*{FIELD_SEPARATOR}?|(?:{names})\s*{FIELD_SEPARATOR})")
    for names in FIELD_NAMES.values()
]
FIELD_START = rf"(?:{OPEN_BRACKET}\s*(?:" + "|".join(FIELD_NAMES.values()) + rf")\s*{CLOSE_BRACKET}\s*{FIELD_SEPARATOR}?|(?:" + "|".join(FIELD_NAMES.values()) + rf")\s*{FIELD_SEPARATOR})"
ORDER_PATTERNS = [
    # 编号标题可含加急说明、表情或地区+渠道+字母前缀；下一行必须是明确字段。
    re.compile(rf"(?m)^[ \t]*(?P<number>(?:[\u4e00-\u9fff]{{2,8}}(?:[A-Z]{{1,4}})?\d{{5,10}}|[A-Z]{{1,4}}\d{{3,10}}(?:-\d{{1,4}})?))(?![A-Z\d_-])(?=[^\r\n]*\r?\n[ \t]*(?:{FIELD_START}|信息\s*[:：]))", re.I),
    re.compile(r"(?m)^[ \t]*(?P<number>wz\d{3,10})(?!\d)(?=[ \t]*\r?$)", re.I),
    re.compile(rf"(?<![A-Za-z0-9])(?P<number>wz\d{{3,10}})(?!\d)(?=\s*(?:{FIELD_START}|{OPEN_BRACKET}|小学|初中|高中|[小初高][一二三四五六1-6]))", re.I),
    re.compile(r"(?:订单(?:编号|号)?|单号)\s*[:：#]?\s*(?P<number>[A-Z0-9][A-Z0-9_-]{2,})", re.I),
    # 地区 + 编号单独占一行，或后面紧接方括号标题。
    re.compile(rf"(?m)^[ \t]*(?P<number>[\u4e00-\u9fff]{{2,8}}\d{{5,10}})(?=[ \t]*(?:\r?$|{OPEN_BRACKET}))"),
    # 即使没有换行，编号后面的字段标题仍可作为边界证据。
    re.compile(rf"(?P<number>[\u4e00-\u9fff]{{2,8}}\d{{5,10}})(?=\s*{FIELD_START})"),
    re.compile(rf"(?P<number>[A-Z]{{1,4}}\d{{5,10}}(?:-\d{{1,4}})?)(?![\d-])(?=\s*(?:$|[\r\n]|{OPEN_BRACKET}|{FIELD_START}|小学|初中|高中|[小初高][一二三四五六1-6]))"),
]
SEPARATOR = re.compile(r"(?m)^[ \t]*(?:[-—=]{3,}|[一二三四五六七八九十\d]+[、.)）])[ \t]*$")
GRADE_PATTERN = re.compile(r"小学|初中|高中|小[一二三四五六1-6]|初[一二三1-3]|高[一二三1-3]")
PRICE_PATTERN = re.compile(r"\d+(?:[-～~]\d+)?\s*元?\s*/\s*\d*\s*(?:小时|课时|h)", re.I)


def find_order_markers(text: str) -> list[OrderMarker]:
    matches = sorted(
        (OrderMarker(m.start(), m.end(), m.group("number")) for pattern in ORDER_PATTERNS for m in pattern.finditer(text)),
        key=lambda marker: (marker.start, -marker.end),
    )
    markers: list[OrderMarker] = []
    for marker in matches:
        if re.fullmatch(r"[A-Z]{1,4}\d{5,10}(?:-\d{1,4})?", marker.number):
            line_start = text.rfind("\n", 0, marker.start) + 1
            prefix = text[line_start:marker.start].strip()
            suffix = text[marker.end:]
            if re.match(rf"[ \t]*{OPEN_BRACKET}", suffix):
                marker.is_start = True
            elif prefix and (read_titles(prefix) or GRADE_PATTERN.search(prefix) or PRICE_PATTERN.search(prefix)):
                marker.is_start = False
            elif text[:marker.start].strip() and not suffix.strip() and (read_titles(text[:marker.start]) or GRADE_PATTERN.search(text[:marker.start])):
                marker.is_start = False
        # 仅合并同一位置被多个规则命中的线索，编号相同的两条订单仍保留。
        if not markers or marker.start >= markers[-1].end:
            markers.append(marker)
    return markers


def _needs_model_split(text: str) -> bool:
    # 多个费用标题可属于同一订单，不单凭薪资数量推断多个订单。
    repeated = {name: len(pattern.findall(text)) > 1 for name, pattern in zip(FIELD_NAMES, FIELD_PATTERNS)}
    if repeated["student"] or repeated["subject"] or sum(repeated[name] for name in ("grade", "schedule", "teacher", "address")) >= 2:
        return True
    # 无字段标题的简写订单也可能整段粘连。
    return not read_titles(text) and len(GRADE_PATTERN.findall(text)) > 1 and len(PRICE_PATTERN.findall(text)) > 1


def is_order_notice(text: str) -> bool:
    """只跳过已知宣传/批次标题；陌生文本必须保留供复核。"""
    if read_titles(text) or GRADE_PATTERN.search(text) or PRICE_PATTERN.search(text) or read_fields(text):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and all(re.fullmatch(r"[\u4e00-\u9fff]{0,12}(?:大学生单|家教单|家教订单)", line) or re.match(r"#.*(?:诚招|回收|小助理)", line) for line in lines)


def split_orders(text: str) -> list[Segment]:
    text = text.strip()
    if not text:
        return []
    # 空行可出现在同一订单的字段之间；保留原始分隔字符并合并字段续段。
    chunks: list[str] = []
    for block in SEPARATOR.split(text):
        spans = list(re.finditer(r"\r?\n[ \t]*\r?\n+", block))
        starts = [0] + [match.end() for match in spans]
        ends = [match.start() for match in spans] + [len(block)]
        group_start = starts[0]
        previous_end = 0
        for start, end in zip(starts, ends):
            paragraph = block[start:end].strip()
            if not paragraph:
                continue
            previous = block[group_start:previous_end].strip()
            current_fields = read_fields(paragraph)
            previous_fields = read_fields(previous)
            current_categories = {field.category for field in current_fields}
            previous_categories = {field.category for field in previous_fields}
            continuation = bool(previous and current_fields and not find_order_markers(paragraph)
                                and not (current_categories & previous_categories - {"fee"}))
            if current_fields and current_fields[0].category in {"student", "grade"} and previous_categories & {"student", "grade", "subject"}:
                continuation = False
            if previous and not continuation:
                chunks.append(previous)
                group_start = start
            previous_end = end
        last = block[group_start:previous_end].strip()
        if last:
            chunks.append(last)
    segments: list[Segment] = []
    for chunk in chunks:
        markers = [marker for marker in find_order_markers(chunk) if marker.is_start]
        starts = [0] + [marker.start for marker in markers[1:]]
        if markers and markers[0].start > 0:
            prefix = chunk[:markers[0].start]
            if any(pattern.search(prefix) for pattern in FIELD_PATTERNS) or read_titles(prefix) or GRADE_PATTERN.search(prefix) and PRICE_PATTERN.search(prefix):
                starts.append(markers[0].start)
        for title in read_titles(chunk):
            if not any(marker.end <= title.start and not chunk[marker.end:title.start].strip() for marker in markers):
                starts.append(title.start)
        starts = sorted(set(starts))
        for start, end in zip(starts, starts[1:] + [len(chunk)]):
            piece = chunk[start:end].strip()
            if piece and not is_order_notice(piece):
                segments.append(Segment(piece, _needs_model_split(piece)))
    return segments


def numbered_lines(text: str) -> str:
    return "\n".join(f"L{index:04d}\t{line}" for index, line in enumerate(text.splitlines(), 1))


def resolve_model_boundaries(text: str, masked_text: str, boundaries: list[OrderBoundary]) -> list[int]:
    """把模型复制的行内片段映射回本地原文，不接受臆造或有歧义的片段。"""
    if len(text) != len(masked_text) or not boundaries:
        raise ValueError("无法验证模型订单边界")
    lines = masked_text.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    starts: list[int] = []
    for boundary in boundaries:
        if boundary.line > len(lines):
            raise ValueError("模型返回了不存在的行")
        line = lines[boundary.line - 1].rstrip("\r\n")
        anchor = boundary.anchor
        if not anchor.strip("█ \t") or line.count(anchor) != 1:
            raise ValueError("模型返回的边界片段不唯一或不在原文中")
        starts.append(offsets[boundary.line - 1] + line.index(anchor))
    if starts[0] != 0 or starts != sorted(set(starts)):
        raise ValueError("模型边界必须按原文顺序覆盖整段订单")
    return starts


def apply_model_boundaries(segment: Segment, starts: list[int]) -> list[Segment]:
    text = segment.raw_text
    if not starts or starts[0] != 0 or starts != sorted(set(starts)) or any(type(i) is not int or i < 0 or i >= len(text) for i in starts):
        return [Segment(text, True)]
    if len(starts) == 1:
        return [Segment(text, True)]
    pieces = [text[a:b].strip() for a, b in zip(starts, starts[1:] + [len(text)])]
    if not all(pieces):
        return [Segment(text, True)]
    # 原文定位可验证，但模型的语义边界仍保留待复核标记。
    return [Segment(piece, True) for piece in pieces]
