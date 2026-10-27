"""地址原文校验与相对方位识别；检索词不回写原始地址。"""

import re

from .locations import NOT_LOCATION, location_spans
from .privacy import mask_for_model
from .text_fields import LABEL, read_fields


ONLINE_ADDRESS = re.compile(r"(?:线上|网课|远程)(?:辅导|上课|授课|教学)?")
ADDRESS_NON_LOCATION = re.compile(
    r"(?:信息|需求|孩子情况|费用|课酬|薪酬|微信(?:号)?|手机号?|电话|联系方式|联系人)\s*[:：★☆]|"
    r"(?:辅导|补习)(?!中心|学校|机构|班)|作业|教材|讲题|讲课|浙教版|人教版|苏教版|"
    r"学员|学生|男孩|女孩|男生|女生|[男女](?:老师|大学生)|认真负责|有耐心|"
    r"基础(?:薄弱|一般|差)|成绩|每周|周末|(?:老师|教师|教员)(?:要求|薪水|课费)|"
    r"(?<!\d)1[3-9](?:[ \t-]*\d){9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"\d+(?:\.\d+)?\s*(?:元(?:/|每)?(?:小时|课时|次)?|/\s*[hH])"
)
NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万半]+)"
DISTANCE_VALUE = rf"{NUMBER}(?:\s*[-—~～至到]\s*{NUMBER})?\s*(?:公里|千米|公尺|米|km(?![a-z])|m(?![a-z]))"
DISTANCE = rf"(?:大约|大概|约)?\s*{DISTANCE_VALUE}(?:\s*[-—~～至到]\s*(?:{DISTANCE_VALUE}|{NUMBER}))?(?:左右|以内|以外|内|外|范围)?"
DIRECTION = rf"(?:(?:以|往|向)?(?:东北|东南|西北|西南|[东西南北])(?:方向|侧|边|面)(?:偏[东西南北])?|以[东西南北]|(?:往|向)?(?:东北|东南|西北|西南|[东西南北])(?:偏[东西南北])?(?=\s*(?:大约|约)?\s*{DISTANCE_VALUE}))"
RELATIVE_WORD = r"附近|对面|旁边|边上|周边|周围|左右|旁|一带|周遭|斜对面|隔壁"
# 保留近江、近水等专名；单字“近”仅在开头或明确行政区/道路前缀后解释。
NEARBY = re.compile(
    rf"{DISTANCE}|斜对面|{RELATIVE_WORD}|{DIRECTION}|"
    r"(?P<near_prefix>^(?:[\u4e00-\u9fff]{2,}?(?:街道|省|市|区|县|镇|乡|路|街|巷)[^近，,；;。()（）]*?)?)"
    rf"(?:距离|距|靠近|临近|邻近|近(?!江|水)|(?:大约在|大概在|约在|大约|约(?!克|翰))(?!\s*{DISTANCE_VALUE}))(?=.)",
    re.IGNORECASE,
)


def valid_address(value: str | None) -> bool:
    """只接受单行地点，不从污染候选中猜测或截取地点。"""
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        return False
    if re.search(r"[\r\n\v\f\x85\u2028\u2029█]", value):
        return False
    compact = re.sub(r"[ \t\u3000]+", "", value)
    return not (ONLINE_ADDRESS.fullmatch(compact) or LABEL.search(value)
                or mask_for_model(value, include_addresses=True) != value
                or NOT_LOCATION.search(compact) or ADDRESS_NON_LOCATION.search(value))


def address_query(address: str) -> str:
    """从原文删去相对方位和距离，只查明确出现的地名，不推算坐标。"""
    nearby = NEARBY.search(address)
    if not nearby:
        return address
    if nearby.group("near_prefix") is not None:
        prefix = nearby.group("near_prefix")
        anchor = address[nearby.end():]
        suffix = NEARBY.search(anchor)
        return (prefix + (anchor[:suffix.start()] if suffix else anchor)).strip(" ，,；;()（）")
    return address[:nearby.start()].strip(" ，,；;()（）")


def validate_model_address(value: str | None, text: str) -> str | None:
    """只接受当前订单中的连续原文；恢复同一处被模型遗漏的范围描述。"""
    if not isinstance(value, str):
        return None
    value = value.strip()
    safe_text = mask_for_model(text, include_addresses=True)
    if not valid_address(value) or value not in safe_text:
        return None
    for span in location_spans(text):
        if value in span.value and valid_address(span.value) and span.value in safe_text:
            return span.value
    # 无地址标题时仍由模型找地点；只扩展同一语句，不拼接地名或跨行。
    for match in re.finditer(re.escape(value), text):
        start, end = match.span()
        line_start = max(text.rfind("\n", 0, start), text.rfind("\r", 0, start)) + 1
        line_end = re.search(r"[\r\n]", text[end:])
        line_end = end + line_end.start() if line_end else len(text)
        before, after = text[line_start:start], text[end:line_end]
        context = re.split(r"[，,；;。:：]", before)[-1]
        has_location_intro = bool(re.search(r"家住|住在|位于|上课在|上课地点在|地点在", context))
        context = re.sub(r"^.*?(?:家住|住在|位于|上课在|上课地点在|地点在)", "", context)
        relative = NEARBY.search(context + value)
        prefix = context if (relative and relative.end() <= len(context) or has_location_intro and valid_address(context)) else ""
        suffix = re.match(rf"[ \t]*(?:(?:斜对面|{RELATIVE_WORD}|{DIRECTION}|{DISTANCE})[ \t]*)+", after, re.IGNORECASE)
        candidate = prefix + value + (suffix.group().rstrip() if suffix else "")
        # 科目、时间、教师要求中的普通短词不是上课地址。
        fields = read_fields(text[line_start:line_end])
        in_other_field = any(field.category != "address" and field.value_start <= start - line_start < field.value_end for field in fields)
        if in_other_field and not re.search(r"家住|住在|位于|上课在|地点|地址", before):
            continue
        if valid_address(candidate) and candidate in safe_text:
            return candidate
    return None
