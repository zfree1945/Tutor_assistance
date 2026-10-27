"""识别常见订单字段标题，保留原文证据与字符位置。"""

import re
from dataclasses import dataclass


FIELD_NAMES = {
    "subject": "辅导科目|年级科目|科目",
    "grade": "年级性别|年级",
    "student": "学员情况|学生情况|学员|学生|对象",
    "schedule": "时间安排|上课时间|时间次数|时间",
    "fee": "老师课费|老师薪水|薪资待遇|课费报酬|课时费|时薪|课费|薪水|待遇|薪资|报酬",
    "teacher": "老师要求|教员要求|教师要求|要求",
    "address": "上课地址|学员地址|联系地址|上课地点|辅导地点|地址|地点|住址|位置",
    "detail": "详情|备注|补充说明|情况",
}
OPEN_BRACKET = r"[【\[〖〔［「]"
CLOSE_BRACKET = r"[】\]〗〕］」]"
FIELD_SEPARATOR = r"[:：★☆]"
ALL_NAMES = "|".join(sorted({name for names in FIELD_NAMES.values() for name in names.split("|")}, key=len, reverse=True))
LABEL = re.compile(rf"(?:{OPEN_BRACKET}\s*(?P<bracket>{ALL_NAMES})\s*{CLOSE_BRACKET}\s*{FIELD_SEPARATOR}?|(?P<plain>{ALL_NAMES})\s*{FIELD_SEPARATOR})")


@dataclass
class TextField:
    category: str
    value: str
    source: str
    value_start: int
    value_end: int


def address_value_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """地址只取本行；同一行的后续字段也不能并入地址。"""
    newline = re.search(r"[\r\n]", text[start:end])
    if newline:
        end = start + newline.start()
    following = LABEL.search(text, start, end)
    if following:
        end = following.start()
    whitespace = " \t\u3000，,；;"
    start += len(text[start:end]) - len(text[start:end].lstrip(whitespace))
    end = start + len(text[start:end].rstrip(whitespace))
    return start, end


def read_fields(text: str) -> list[TextField]:
    matches = list(LABEL.finditer(text))
    fields: list[TextField] = []
    for index, match in enumerate(matches):
        name = match.group("bracket") or match.group("plain")
        category = next(key for key, names in FIELD_NAMES.items() if name in names.split("|"))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        start = match.end()
        if category == "address":
            # 标签正则中的空白可能跨行，空标题不能借用下一行的正文。
            newline = re.search(r"[\r\n]", text[match.start():end])
            if newline:
                end = match.start() + newline.start()
            start, end = address_value_bounds(text, min(start, end), end)
        value = text[start:end].strip(" \t\r\n，,；;")
        fields.append(TextField(category, value, text[match.start():end].strip(), start, end))
    return fields
