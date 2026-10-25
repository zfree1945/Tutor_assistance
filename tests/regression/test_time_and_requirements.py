"""用户格式周边的反例：排除窗口、数字钟点、半小时、要求与学科上下文。"""
import pytest

from tutor_screen.local_parser import parse_local
from tutor_screen.matching import match_order
from tutor_screen.models import Criteria
from tutor_screen.pricing import parse_price
from helpers import order


@pytest.mark.parametrize("phrase,hours", [("一次两个半小时", 2.5), ("每次三个半小时", 3.5), ("每次一个半小时", 1.5), ("每次两小时半", 2.5), ("每次两小时三十分钟", 2.5), ("一次90分钟", 1.5)])
def test_duration_variants(phrase, hours):
    assert parse_local("初中数学 线上\n时间：周六晚上 " + phrase).lesson_hours == hours


@pytest.mark.parametrize("quote,rate", [("200两个半小时", 80), ("200元/两个半小时", 80), ("180/90分钟", 120), ("150元一个半小时", 100)])
def test_price_duration_variants(quote, rate):
    assert parse_price(quote).hourly_rate == rate


@pytest.mark.parametrize("phrase,slots", [("周一到周五19:00-21:30", ["晚上"]), ("周六9:00～11:00", ["上午"]), ("周六14:00至16:00", ["下午"]), ("周六11:00-13:00", ["上午", "下午"]), ("周六下午12:00-13:00", ["下午"]), ("周六晚18:30～20:30", ["晚上"])])
def test_clock_windows(phrase, slots):
    assert parse_local("小学数学 150/h 线上\n时间：" + phrase).time_slots == slots


@pytest.mark.parametrize("phrase", ["周六除了下午1-3点，其他时间都可以", "周六下午1-3点不行，其他时间可协商", "周六不要安排下午1-3点，其他时间均可"])
def test_excluded_windows_never_become_fixed_positive_afternoon(phrase):
    value = order("小学数学 150/h 线上 每周1次 " + phrase)
    assert value.time_slots == []
    assert getattr(value, "excluded_times", [])
    for slots in (["上午"], ["下午"], ["晚上"]):
        result = match_order(value, Criteria(days=[6], time_slots=slots))
        assert result and result.match_status == "可能匹配"
        assert "排除时段" in result.uncertain_fields
    assert match_order(value, Criteria(days=[7])) is None


@pytest.mark.parametrize("phrase,gender", [("要求女在读大学生", "女"), ("要求男在读大学生", "男"), ("要求：在读女大学生", "女"), ("要求女生，有经验", "女"), ("要求男老师或女老师均可", "不限"), ("要求不限男女", "不限"), ("要求不要男老师", "女"), ("要求不要女老师", "男")])
def test_teacher_requirement_variants(phrase, gender):
    assert parse_local("小学数学 150/h 线上 学生为女生，" + phrase).teacher_gender == gender


def test_teacher_expertise_does_not_add_order_subjects():
    parsed = parse_local("【小学语文，鹿城模拟小区】四年级，要求在读大学生，擅长数学英语，理科强一些")
    assert parsed.subjects == ["语文"]


def test_conflicting_lesson_durations_are_not_guessed():
    value = parse_local("小学数学 150两小时，一次小时，周六晚上")
    assert value.hourly_rate == 75 and value.lesson_hours is None
    assert "每次时长" in value.uncertain_fields
