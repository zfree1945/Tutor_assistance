"""整批处理，单条失败不会中断后续订单。"""

from collections.abc import Callable
import re

from openai import APIConnectionError, APITimeoutError, AuthenticationError, PermissionDeniedError, RateLimitError

from .llm_client import ModelReplyError, extract_order, try_split
from .local_parser import normalize_local_fields, parse_local
from .models import ExtractedOrder, Order
from .segmentation import Segment, apply_model_boundaries, find_order_markers, split_orders
from .text_fields import read_fields


class TooManyOrders(ValueError):
    pass


def _is_non_order(text: str) -> bool:
    """仅忽略无编号、无字段且不足八个汉字的片段。"""
    return (not find_order_markers(text) and not read_fields(text)
            and len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U000323af]", text)) < 8)


def repair_merged_orders(orders: list[Order]) -> list[Order]:
    """已保存订单只按明确编号修复粘连；保留其他订单，不请求模型。"""
    repaired = []
    used_ids = {order.order_id for order in orders}
    for order in orders:
        markers = [marker for marker in find_order_markers(order.raw_text) if marker.is_start]
        if len(markers) < 2:
            repaired.append(order)
            continue
        starts = [0, *(marker.start for marker in markers[1:])]
        for index, (start, end) in enumerate(zip(starts, starts[1:] + [len(order.raw_text)]), 1):
            raw = order.raw_text[start:end].strip()
            number = index
            order_id = order.order_id if index == 1 else f"{order.order_id}-S{number:02d}"
            while index > 1 and order_id in used_ids:
                number += 1
                order_id = f"{order.order_id}-S{number:02d}"
            used_ids.add(order_id)
            try:
                parsed = parse_local(raw)
                source = "local"
            except Exception:
                parsed = ExtractedOrder(uncertain_fields=["本地解析"])
                source = "failed"
            repaired.append(Order(**parsed.model_dump(), order_id=order_id, raw_text=raw,
                                  extraction_source=source, extraction_note="按原文明确订单编号修复粘连，已本地提取，请复核。"))
    return repaired


def _failure_reason(error: Exception) -> str:
    if isinstance(error, APITimeoutError):
        return "模型接口请求超时"
    if isinstance(error, AuthenticationError):
        return "模型接口认证失败，请检查 API Key"
    if isinstance(error, PermissionDeniedError):
        return "模型接口无访问权限，请检查模型与账户权限"
    if isinstance(error, RateLimitError):
        return "模型接口限流或额度不足"
    if isinstance(error, APIConnectionError):
        return "无法连接模型接口，请检查网络和接口地址"
    return "模型接口调用失败"


def process_batch(
    text: str, api_key: str, base_url: str, model: str,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[Order]:
    candidates = split_orders(text)
    segments = [segment for segment in candidates if not _is_non_order(segment.raw_text)]
    ignored = len(candidates) - len(segments)
    if progress:
        progress("识别订单边界", 0, len(segments))
    if len(segments) > 300:
        raise TooManyOrders("一次最多处理 300 条订单，请分批导入。")
    resolved: list[Segment] = []
    for index, segment in enumerate(segments, 1):
        if segment.uncertain:
            if progress:
                progress("模型区分粘连订单", index, len(segments))
            try:
                starts = try_split(segment.raw_text, api_key, base_url, model)
                resolved.extend(apply_model_boundaries(segment, starts or []))
            except Exception:
                resolved.append(Segment(segment.raw_text, True))
        else:
            resolved.append(segment)
        if len(resolved) > 300:
            raise TooManyOrders("一次最多处理 300 条订单，请分批导入。")
    retained = [segment for segment in resolved if not _is_non_order(segment.raw_text)]
    ignored += len(resolved) - len(retained)
    resolved = retained
    if progress:
        if ignored:
            progress("已忽略非订单内容", ignored, 0)
        progress("订单边界识别完成", len(resolved), len(resolved))
    orders: list[Order] = []
    failures = 0
    for index, segment in enumerate(resolved, 1):
        local_failed = False
        try:
            local = parse_local(segment.raw_text)
        except Exception:
            # 一条异常输入不能阻止后续订单，错误详情不进入页面或导出。
            local_failed = True
            local = ExtractedOrder(uncertain_fields=["本地解析"])
        source = "local"
        note = ""
        if failures < 3:
            try:
                parsed = extract_order(segment.raw_text, api_key, base_url, model)
                failures = 0
                source = "model"
                normalize_local_fields(parsed, segment.raw_text, local, preserve_model_address=True)
                if "模型地址未通过原文校验" in parsed.uncertain_fields:
                    note = "模型地址未通过原文校验，已回退本地地址规则，请复核。"
                elif "地址存在多个候选" in parsed.uncertain_fields:
                    note = "本地与模型地址不一致，已保留本地明确地址，请核对原文。"
            except ModelReplyError as error:
                # 模型内容错误只影响这一条，不能让后续正常订单停用模型。
                failures = 0
                parsed = local
                note = str(error) + "；本条已使用本地规则，需复核。"
            except Exception as error:
                failures += 1
                parsed = local
                note = _failure_reason(error) + "；本条已使用本地规则，需复核。"
        else:
            parsed = local
            note = "模型连续失败，剩余订单已使用本地规则，需复核。"
        if local_failed:
            note = "本地字段解析失败，已保留原文供复核。" + note
            if source == "local":
                source = "failed"
        payload = parsed.model_dump()
        orders.append(Order(
            **payload, order_id=f"ORD-{index:04d}", raw_text=segment.raw_text,
            split_uncertain=segment.uncertain, extraction_source=source, extraction_note=note,
        ))
        if progress:
            progress("逐条解析", index, len(resolved))
    if progress:
        progress("执行本地筛选", len(orders), len(orders))
    return orders
