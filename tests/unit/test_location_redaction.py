"""验证原文范围与两个模型请求入口的实际脱敏载荷。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tutor_screen.llm_client import extract_order, try_split
from tutor_screen.local_parser import parse_local
from tutor_screen.locations import location_spans
from tutor_screen.privacy import mask_for_model
from tutor_screen.segmentation import numbered_lines, split_orders


@pytest.mark.parametrize("label", ["地点:", "联系地址：", "地址★", "地址"])
@pytest.mark.parametrize("separator", ["\n", "\r\n", " "])
def test_only_address_span_is_hidden_and_business_text_is_preserved(label, separator):
    raw = f"{label}温州龙湾滨海宝龙世家　{separator}对象:初一学生，要求思路清晰，辅导数学/科学/英语"
    span, = location_spans(raw)
    assert raw[span.start:span.end] == span.value == "温州龙湾滨海宝龙世家"
    expected = raw[:span.start] + "█" * (span.end - span.start) + raw[span.end:]
    assert mask_for_model(raw) == expected


@pytest.mark.parametrize("path", ["extract", "split"])
def test_actual_user_sample_requests_hide_all_locations_and_keep_student_line(path):
    sample = (Path(__file__).parent / "fixtures/sixteen_orders_with_decoration.txt").read_text(encoding="utf-8")
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"boundaries": []}' if path == "split" else '{}'))])
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = reply
        if path == "split":
            try_split(sample, "synthetic", "https://example.test", "synthetic")
        else:
            for segment in split_orders(sample):
                extract_order(segment.raw_text, "synthetic", "https://example.test", "synthetic")
        payloads = [call.kwargs["messages"][-1]["content"] for call in client.return_value.chat.completions.create.call_args_list]
    sent = "\n".join(payloads)
    for span in location_spans(sample):
        assert span.value not in sent
    assert "对象:初一学生，辅导数学/科学/英语，浙教版学生现在每课85分以上" in sent
    assert "70-90元/小时" in sent
    if path == "split":
        assert payloads == [numbered_lines(mask_for_model(sample))]


def test_repeated_title_and_explicit_addresses_keep_exact_offsets():
    raw = "【初中数学，龙湾测试花园】\n联系地址：龙湾测试花园\n对象:初一学生"
    chars = list(raw)
    for span in location_spans(raw):
        assert raw[span.start:span.end] == span.value
        chars[span.start:span.end] = "█" * (span.end - span.start)
    assert mask_for_model(raw) == "".join(chars)


def test_unlabeled_address_and_contacts_still_hidden_without_masking_student():
    raw = "地点:温州龙湾滨海宝龙世家\r\n对象:初一学生，要求思路清晰\r\n模拟大厦3栋502室\r\n电话：13800138000\r\n微信：test_contact"
    masked = mask_for_model(raw)
    for value in ["温州龙湾滨海宝龙世家", "模拟大厦3栋502室", "13800138000", "test_contact"]:
        assert value not in masked
    assert "对象:初一学生，要求思路清晰" in masked
    assert len(masked) == len(raw)
    assert [i for i, ch in enumerate(raw) if ch in "\r\n"] == [i for i, ch in enumerate(masked) if ch in "\r\n"]


def test_extra_privacy_ranges_are_exact_and_do_not_become_order_addresses():
    raw = "对象:初一学生，住在测试路88号，要求思路清晰\n模拟大厦3栋502室\n科目:数学"
    assert parse_local(raw).address is None
    assert location_spans(raw) == []
    spans = location_spans(raw, for_redaction=True)
    assert spans and all(raw[span.start:span.end] == span.value for span in spans)
    masked = mask_for_model(raw)
    assert "测试路88号" not in masked and "模拟大厦3栋502室" not in masked
    assert "要求思路清晰" in masked and "科目:数学" in masked
