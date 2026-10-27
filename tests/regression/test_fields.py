"""PAY/FLD/TIME：50条独立语义标注样例，预期不来自被测解析器。"""
import itertools
import random

import pytest

from tutor_screen.local_parser import parse_local
from tutor_screen.pricing import parse_price


# 每行是原文与人工预期字段，测试失败时保留原文和字段差异。
GOLDEN = [
    ("小学数学 180/2h", {"grade": "小学", "subjects": ["数学"], "hourly_rate": 90}),
    ("初中英语 300元/2小时", {"grade": "初中", "hourly_rate": 150}),
    ("高中物理 150元一个小时", {"grade": "高中", "hourly_rate": 150}),
    ("小学语文 每次2小时 1802h", {"hourly_rate": 90, "lesson_hours": 2}),
    ("小学语文 1802h", {"hourly_rate": 90, "price_incomplete": False}),
    ("初二科学 140-160/2h", {"hourly_rate_min": 70, "hourly_rate_max": 80}),
    ("初中数学 130/h 150/h 面议", {"hourly_rate_min": 130, "hourly_rate_max": 150, "price_incomplete": True}),
    ("小学英语 149.99/h", {"hourly_rate": 149.99}),
    ("小学英语 150/h", {"hourly_rate": 150}),
    ("小学英语 150.01/h", {"hourly_rate": 150.01}),
    ("小学数学 一周一次", {"days": [], "weekly_sessions": 1}),
    ("初中数学 每周三次", {"days": [], "weekly_sessions": 3}),
    ("高中数学 两周一次", {"days": [], "weekly_sessions": None}),
    ("高中数学 每两周一次", {"days": [], "weekly_sessions": None}),
    ("初中英语 每周2次 周五周六晚上", {"days": [5, 6], "weekly_sessions": 2, "time_slots": ["晚上"]}),
    ("初中英语 周一到周五", {"days": [1, 2, 3, 4, 5]}),
    ("初中英语 周五至周日", {"days": [5, 6, 7]}),
    ("初中英语 星期一 星期三", {"days": [1, 3]}),
    ("初中英语 周六或者周日", {"days": [6, 7], "day_options": [[6], [7]]}),
    ("初中英语 一次3小时 时间可协商", {"lesson_hours": 3, "days": [], "time_slots": []}),
    ("〖学员〗四年级女生\n〖科目〗语文", {"grade": "小学", "subjects": ["语文"], "teacher_gender": "不限"}),
    ("【学员】初三男\n【科目】科学数学英语", {"grade": "初中", "subjects": ["数学", "英语", "科学"]}),
    ("[学生]五年级\n[科目]数学\n[要求]女大学生", {"grade": "小学", "teacher_gender": "女"}),
    ("〔学生〕高二女\n〔要求〕男老师", {"grade": "高中", "teacher_gender": "男"}),
    ("［学生］六年级\n［要求］男女不限", {"grade": "小学", "teacher_gender": "不限"}),
    ("「学生」七年级\n「科目」英文", {"grade": "初中", "subjects": ["英语"]}),
    ("小学数学 女学生基础差", {"teacher_gender": "不限"}),
    ("初中英语 男学生基础差", {"teacher_gender": "不限"}),
    ("小学语文\n要求：女专职老师", {"teacher_gender": "女"}),
    ("高中数学\n要求：男大学生", {"teacher_gender": "男"}),
    ("小学数学 上门150/h 线上130/h", {"mode": "线上/线下均可", "hourly_rate_min": 130, "hourly_rate_max": 150}),
    ("初中英语 仅线上 不上门", {"mode": "线上"}),
    ("高中物理 线下 不支持线上", {"mode": "线下"}),
    ("小学数学 网课", {"mode": "线上"}),
    ("初中英语 面授", {"mode": "线下"}),
    ("小学数学\n地址：测试路88号\n要求：女老师", {"address": "测试路88号", "mode": "线下"}),
    ("初中全科 200/h", {"subjects": ["全科"], "hourly_rate": 200}),
    ("高中数理化 200/h", {"subjects": ["数理化"]}),
    ("初中道法", {"subjects": ["政治"]}),
    ("高中历史地理", {"subjects": ["历史", "地理"]}),
    ("小学数学 一次半小时", {"lesson_hours": 0.5}),
    ("小学数学 每次两小时", {"lesson_hours": 2}),
    ("初中数学 每次1.5小时", {"lesson_hours": 1.5}),
    ("初中数学 时间：一周1次，一次1.5小时，时间可协商", {"weekly_sessions": 1, "lesson_hours": 1.5, "days": [], "time_slots": []}),
    ("初中数学 每周1-2次", {"weekly_sessions": None, "days": []}),
    ("订单号 A100 小学数学", {"order_number": "A100"}),
    ("温州433255\n辅导科目：高三数学", {"order_number": "温州433255", "grade": "高中"}),
    ("S556313【初中全科】", {"order_number": "S556313", "grade": "初中"}),
    ("订单号 A101 小学语文 时薪：0", {"hourly_rate": 0}),
    ("初中数学 线上\n时间：周六晚上或周日上午", {"days": [6, 7], "day_options": [[6], [7]], "time_slots": ["上午", "晚上"]}),
]


@pytest.mark.parametrize("raw,expected", GOLDEN, ids=[f"GOLD-{i:02}" for i in range(1, 51)])
def test_golden_fields(raw, expected):
    parsed = parse_local(raw)
    actual = {field: getattr(parsed, field) for field in expected}
    assert actual == expected, raw


@pytest.mark.parametrize("raw", ["-180/h", "时薪：-180", "180/-2h", "180/0h", "200-100/h", "时薪：200-100", "NaN/h", "inf/h"])
def test_invalid_price_never_becomes_known(raw):
    price = parse_price(raw)
    assert price.lower is None and price.upper is None, (raw, price)


def test_quote_permutation_and_scaling_properties():
    rng = random.Random(20261010)
    for size in (2, 3, 5):
        quotes = [(60, 2), (135, 3), (180, 2), (420, 2), (1000, 5)][:size]
        for permutation in itertools.islice(itertools.permutations(quotes), 15):
            raw = "；".join(f"{amount}/{hours}h" for amount, hours in permutation)
            price = parse_price(raw)
            assert (price.lower, price.upper) == (30, max(a / h for a, h in quotes))
    for _ in range(50):
        amount, hours, factor = rng.randint(20, 1000), rng.randint(1, 5), rng.randint(2, 10)
        assert parse_price(f"{amount}/{hours}h").hourly_rate == parse_price(f"{amount*factor}/{hours*factor}h").hourly_rate
