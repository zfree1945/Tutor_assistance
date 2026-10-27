"""二次审查：原修复周边的反例与交叉矛盾，独立保存首次失败记录。"""
from unittest.mock import patch

import pytest

from tutor_screen.llm_client import ModelReplyError, extract_order
from tutor_screen.local_parser import parse_local
from tutor_screen.models import ExtractedOrder
from tutor_screen.pipeline import process_batch
from tutor_screen.privacy import mask_for_model
from helpers import response


def test_inline_address_mask_preserves_business_fields():
    raw = "地址：测试路88号 科目：数学 时薪：150 时间：周六晚上"
    masked = mask_for_model(raw)
    assert "测试路88号" not in masked
    for value in ["数学", "150", "周六晚上"]:
        assert value in masked


@pytest.mark.parametrize("label", ["女生", "男生", "女，有经验", "男 有经验"])
def test_explicit_teacher_field_gender(label):
    raw = f"小学数学 150/h 线上 要求：{label}"
    assert parse_local(raw).teacher_gender == label[0]


@pytest.mark.parametrize("fee", ["- 180/h", "180/-2h", "时薪：-180", "时薪：NaN", "200-100/h"])
def test_invalid_raw_price_overrides_model_guess(fee):
    raw = f"订单号 A100 小学数学 线上 周六晚上\n课费：{fee}"
    with patch("tutor_screen.pipeline.extract_order", return_value=ExtractedOrder(hourly_rate=180)):
        result = process_batch(raw, "key", "https://example.test", "model")
    assert result[0].hourly_rate is None
    assert result[0].hourly_rate_min is None and result[0].price_incomplete


@pytest.mark.parametrize("payload", [{"days": [6], "day_options": [[7]]}, {"days": [6], "schedule_options": [{"days": [7], "time_slots": ["晚上"]}]}, {"hourly_rate": 50, "hourly_rate_min": 100, "hourly_rate_max": 200}])
def test_contradictory_model_payload_is_rejected(payload):
    with patch("tutor_screen.llm_client._client") as client:
        client.return_value.chat.completions.create.return_value = response(payload)
        with pytest.raises(ModelReplyError):
            extract_order("小学数学", "key", "https://example.test", "model")


def test_multiline_subject_field_retains_all_subjects():
    raw = "学员：初三\n科目：数学\n英语\n时间：周六晚上"
    assert set(parse_local(raw).subjects) == {"数学", "英语"}
