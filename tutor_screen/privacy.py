"""模型请求的等长脱敏；字段提取可保留地址，边界识别仍隐藏地址。"""

import re

from .text_fields import CLOSE_BRACKET, OPEN_BRACKET
from .locations import mask_locations


def mask_for_model(text: str, *, include_addresses: bool = False) -> str:
    # 等长替换让模型给出的字符位置仍可映射到原文。
    def hide(match: re.Match[str]) -> str:
        return "█" * len(match.group())

    patterns = [
        r"(?<!\d)(?:\+?86[ \t-]*)?1[3-9](?:[ \t-]*\d){9}(?!\d)",
        rf"(?:{OPEN_BRACKET}\s*(?:微信(?:号)?|vx|V信|联系(?!地址)(?:人|方式)?|电话|手机|姓名)\s*{CLOSE_BRACKET}|(?:微信(?:号)?|vx|V信|联系(?!地址)(?:人|方式)?|电话|手机|姓名))[ \t]*[:：]?[ \t]*[^\s，,；;。\n]{{2,30}}",
    ]
    result = text if include_addresses else mask_locations(text, "█", for_redaction=True)
    for pattern in patterns:
        result = re.sub(pattern, hide, result, flags=re.IGNORECASE)
    return result
